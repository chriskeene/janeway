__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


import os
import uuid

from django.conf import settings
from django.db import models
from django.core.files.storage import FileSystemStorage

from core import models as core_models


fs = FileSystemStorage(location=settings.MEDIA_ROOT)


def cover_images_upload_path(instance, filename):
    try:
        filename = str(uuid.uuid4()) + '.' + str(filename.split('.')[1])
    except IndexError:
        filename = str(uuid.uuid4())

    path = "press_carousel/"
    return os.path.join(path, filename)


def press_carousel_choices():
    return (
        ('articles', 'Latest Articles'),
        ('news', 'Latest News'),
        ('news_and_articles', 'Latest News and Articles')
    )


class Press(models.Model):
    name = models.CharField(max_length=600)
    domain = models.CharField(max_length=255, default='localhost', unique=True)
    thumbnail_image = models.ForeignKey('core.File', null=True, blank=True, related_name='press_thumbnail_image')
    footer_description = models.TextField(null=True, blank=True)
    main_contact = models.EmailField(default='janeway@voyager.com', blank=False, null=False)
    theme = models.CharField(max_length=255, default='press', blank=False, null=False)
    homepage_news_items = models.PositiveIntegerField(default=5)
    carousel_type = models.CharField(max_length=30, default='articles', choices=press_carousel_choices())
    carousel_items = models.PositiveIntegerField(default=4)
    carousel = models.OneToOneField('carousel.Carousel', related_name='press', null=True, blank=True)
    default_carousel_image = models.ImageField(upload_to=cover_images_upload_path, null=True, blank=True, storage=fs)
    favicon = models.ImageField(upload_to=cover_images_upload_path, null=True, blank=True, storage=fs)
    is_secure = models.BooleanField(default=False, help_text="If the press should redirect to HTTPS, mark this.")

    random_featured_journals = models.BooleanField(default=False)
    featured_journals = models.ManyToManyField('journal.Journal', blank=True, null=True)
    carousel_news_items = models.ManyToManyField('core.NewsItem', blank=True, null=True)

    password_reset_text = models.TextField(blank=True, null=True)
    registration_text = models.TextField(blank=True, null=True)

    def __str__(self):
        return u'%s' % self.name

    def __repr__(self):
        return u'%s' % self.name

    @staticmethod
    def get_press(request):
        try:
            p = Press.objects.all()[:1].get()
            return p
        except BaseException:
            return None

    @staticmethod
    def journals():
        from journal import models as journal_models
        return journal_models.Journal.objects.all()

    @staticmethod
    def users():
        return core_models.Account.objects.all()

    @staticmethod
    def press_url(request):
        return 'http{0}://{1}{2}'.format('s' if request.is_secure() else '',
                                         Press.get_press(request).domain,
                                         ':{0}'.format(request.port) if request != 80 or request.port == 443 else '')

    @staticmethod
    def press_cover(request, absolute=True):
        if request.press.thumbnail_image:
            if absolute:
                return os.path.join(settings.BASE_DIR, 'files', 'press',
                                    str(request.press.thumbnail_image.uuid_filename))
            else:
                return os.path.join('files', 'press', str(request.press.thumbnail_image.uuid_filename))
        else:
            return None

    @staticmethod
    def install_cover(press, request):
        """ Installs the default cover for the press (stored in Files/press/cover.png)

        :param press: the press object
        :param request: the current request or None
        :return: None
        """

        if request:
            owner = request.user if request.user is not None and not request.user.is_anonymous else core_models.Account(id=1)
        else:
            owner = core_models.Account(id=1)

        thumbnail_file = core_models.File(
            mime_type="image/png",
            original_filename="cover.png",
            uuid_filename="cover.png",
            label="Press logo",
            description="Logo for the press",
            owner=owner
        )

        core_models.File.add_root(instance=thumbnail_file)

        press.thumbnail_image = thumbnail_file
        press.save()

    def next_journal_order(self):
        from journal import models as journal_models
        max_number = max([journal.sequence for journal in journal_models.Journal.objects.all()])

        if not max_number:
            return 0
        else:
            return max_number + 1

    @property
    def active_carousel(self):
        """ Renders a carousel for the journal homepage.
        :return: a tuple containing the active carousel and list of associated articles
        """
        import core.logic as core_logic
        carousel_objects = []
        article_objects = []
        news_objects = []

        if self.carousel is None:
            return None, []

        if self.carousel.mode == 'off':
            return self.carousel, []

        # determine the carousel mode and build the list of objects as appropriate
        if self.carousel.mode == "latest":
            article_objects = core_logic.latest_articles(self.carousel, 'press')

        elif self.carousel.mode == "selected":
            article_objects = core_logic.selected_articles(self.carousel)

        elif self.carousel.mode == "news":
            news_objects = core_logic.news_items(self.carousel, 'press', self)

        elif self.carousel.mode == "mixed":
            # news items and latest articles
            news_objects = core_logic.news_items(self.carousel, 'press', self)
            article_objects = core_logic.latest_articles(self.carousel, 'press')

        elif self.carousel.mode == "mixed-selected":
            # news items and latest articles
            news_objects = core_logic.news_items(self.carousel, 'press', self)
            article_objects = core_logic.selected_articles(self.carousel)

        # run the exclusion routine
        if self.carousel.mode != "news" and self.carousel.exclude:
            # remove articles from the list here when the user has specified that certain articles
            # should be excluded
            exclude_list = self.carousel.articles.all()
            excluded = exclude_list.values_list('id', flat=True)
            try:
                article_objects = article_objects.exclude(id__in=excluded)
            except AttributeError:
                for exclude_item in exclude_list:
                    if exclude_item in article_objects:
                        article_objects.remove(exclude_item)

        # now limit the items by the respective amounts
        if self.carousel.article_limit > 0:
            article_objects = article_objects[:self.carousel.article_limit]

        if self.carousel.news_limit > 0:
            news_objects = news_objects[:self.carousel.news_limit]

        # if running in a mixed mode, sort the objects by a mixture of date_published for articles and posted for
        # news items. Note, this has to be done AFTER the exclude procedure above.
        if self.carousel.mode == "mixed-selected" or self.carousel.mode == 'mixed':
            carousel_objects = core_logic.sort_mixed(article_objects, news_objects)
        elif self.carousel.mode == 'news':
            carousel_objects = news_objects
        else:
            carousel_objects = article_objects

        return self.carousel, carousel_objects
