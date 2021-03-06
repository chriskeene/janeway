__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"


from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from core import files, models as core_models
from preprint import models as preprint_models
from security.decorators import article_edit_user_required, production_user_or_editor_required
from submission import forms, models, logic
from events import logic as event_logic
from identifiers import models as identifier_models
from utils import setting_handler
from utils import shared as utils_shared


@login_required
def start(request, type=None):
    competing_interests = setting_handler.get_setting('general', 'submission_competing_interests', request.journal)
    form = forms.ArticleStart(ci=competing_interests)

    if not request.user.is_author(request):
        request.user.add_account_role('author', request.journal)

    if request.POST:
        form = forms.ArticleStart(request.POST, ci=competing_interests)

        if form.is_valid():
            new_article = form.save(commit=False)
            new_article.owner = request.user
            new_article.journal = request.journal
            new_article.current_step = 1
            new_article.article_agreement = logic.get_agreement_text(request.journal)
            new_article.save()

            if type == 'preprint':
                preprint_models.Preprint.objects.create(article=new_article)

            return redirect(reverse('submit_info', kwargs={'article_id': new_article.pk}))

    template = 'submission/start.html'
    context = {
        'form': form
    }

    return render(request, template, context)


@login_required
def submit_submissions(request):
    # gets a list of submissions for the logged in user
    articles = models.Article.objects.filter(owner=request.user).exclude(stage=models.STAGE_UNSUBMITTED)

    template = 'submission/submission_submissions.html'
    context = {
        'articles': articles,
    }

    return render(request, template, context)


@login_required
@article_edit_user_required
def submit_info(request, article_id):
    article = get_object_or_404(models.Article, pk=article_id)

    form = forms.ArticleInfo(instance=article)

    if request.POST:
        form = forms.ArticleInfo(request.POST, instance=article)

        if form.is_valid():
            form.save()
            article.current_step = 2
            article.save()

            return redirect(reverse('submit_authors', kwargs={'article_id': article_id}))

    template = 'submission/submit_info.html'
    context = {
        'article': article,
        'form': form,
    }

    return render(request, template, context)


@staff_member_required
def publisher_notes_order(request, article_id):
    if request.POST:
        ids = request.POST.getlist('note[]')
        ids = [int(_id) for _id in ids]

        article = models.Article.objects.get(pk=article_id)

        for he in article.publisher_notes.all():
            he.sequence = ids.index(he.pk)
            he.save()

    return HttpResponse('Thanks')


@login_required
@article_edit_user_required
def submit_authors(request, article_id):
    article = get_object_or_404(models.Article, pk=article_id)

    if article.current_step < 2 and not request.user.is_staff:
        return redirect(reverse('submit_info', kwargs={'article_id': article_id}))

    form = forms.AuthorForm()
    error, modal = None, None

    if request.GET.get('add_self', None) == 'True':
        new_author = logic.add_self_as_author(request.user, article)
        messages.add_message(request, messages.SUCCESS, '%s added to the article' % new_author.full_name())
        return redirect(reverse('submit_authors', kwargs={'article_id': article_id}))

    if request.POST and 'add_author' in request.POST:
        form = forms.AuthorForm(request.POST)
        modal = 'author'

        author_exists = logic.check_author_exists(request.POST.get('email'))
        if author_exists:
            article.authors.add(author_exists)
            messages.add_message(request, messages.SUCCESS, '%s added to the article' % author_exists.full_name())
            return redirect(reverse('submit_authors', kwargs={'article_id': article_id}))
        else:
            if form.is_valid():
                new_author = form.save(commit=False)
                new_author.username = new_author.email
                new_author.set_password(utils_shared.generate_password())
                new_author.save()
                new_author.add_account_role(role_slug='author', journal=request.journal)
                article.authors.add(new_author)
                messages.add_message(request, messages.SUCCESS, '%s added to the article' % new_author.full_name())

                return redirect(reverse('submit_authors', kwargs={'article_id': article_id}))

    elif request.POST and 'search_authors' in request.POST:
        search = request.POST.get('author_search_text')

        try:
            search_author = core_models.Account.objects.get(Q(email=search) | Q(orcid=search))
            article.authors.add(search_author)
            messages.add_message(request, messages.SUCCESS, '%s added to the article' % search_author.full_name())
        except core_models.Account.DoesNotExist:
            messages.add_message(request, messages.WARNING, 'No author found with those details.')

    elif request.POST:
        correspondence_author = request.POST.get('main-author', None)

        if correspondence_author == 'None':
            messages.add_message(request, messages.WARNING, 'You must select a main author.')
        else:
            author = core_models.Account.objects.get(pk=correspondence_author)
            article.correspondence_author = author
            article.current_step = 3
            article.save()

            return redirect(reverse('submit_files', kwargs={'article_id': article_id}))

    template = 'submission/submit_authors.html'
    context = {
        'error': error,
        'article': article,
        'form': form,
        'modal': modal,
    }

    return render(request, template, context)


@login_required
@article_edit_user_required
def delete_author(request, article_id, author_id):
    article = get_object_or_404(models.Article, pk=article_id)
    author = get_object_or_404(core_models.Account, pk=author_id)
    article.authors.remove(author)

    if article.correspondence_author == author:
        article.correspondence_author = None

    return redirect(reverse('submit_authors', kwargs={'article_id': article_id}))


@login_required
@article_edit_user_required
def submit_files(request, article_id):
    article = get_object_or_404(models.Article, pk=article_id)
    form = forms.FileDetails()

    if article.current_step < 3 and not request.user.is_staff:
        return redirect(reverse('submit_authors', kwargs={'article_id': article_id}))

    error, modal = None, None

    if request.POST:

        if 'delete' in request.POST:
            file_id = request.POST.get('delete')
            file = get_object_or_404(core_models.File, pk=file_id, article_id=article.pk)
            file.delete()
            messages.add_message(request, messages.WARNING, 'File deleted')
            return redirect(reverse('submit_files', kwargs={'article_id': article_id}))

        if 'manuscript' in request.POST:
            form = forms.FileDetails(request.POST)
            uploaded_file = request.FILES.get('file')
            if logic.check_file(uploaded_file, request, form):
                if form.is_valid():
                    new_file = files.save_file_to_article(uploaded_file, article, request.user)
                    article.manuscript_files.add(new_file)
                    new_file.label = form.cleaned_data['label']
                    new_file.description = form.cleaned_data['description']
                    new_file.save()
                    return redirect(reverse('submit_files', kwargs={'article_id': article_id}))
                else:
                    modal = 'manuscript'
            else:
                modal = 'manuscript'

        if 'data' in request.POST:
            for uploaded_file in request.FILES.getlist('file'):
                form = forms.FileDetails(request.POST)
                if form.is_valid():
                    new_file = files.save_file_to_article(uploaded_file, article, request.user)
                    article.data_figure_files.add(new_file)
                    new_file.label = form.cleaned_data['label']
                    new_file.description = form.cleaned_data['description']
                    new_file.save()
                    return redirect(reverse('submit_files', kwargs={'article_id': article_id}))
                else:
                    modal = 'data'

        if 'next_step' in request.POST:
            if article.manuscript_files.all().count() >= 1:
                article.current_step = 4
                article.save()
                return redirect(reverse('submit_review', kwargs={'article_id': article_id}))
            else:
                error = "You must upload a manuscript file."

    template = "submission/submit_files.html"
    context = {
        'article': article,
        'error': error,
        'form': form,
        'modal': modal,
    }

    return render(request, template, context)


@login_required
@article_edit_user_required
def submit_review(request, article_id):
    article = get_object_or_404(models.Article, pk=article_id)

    if article.current_step < 4 and not request.user.is_staff:
        return redirect(reverse('submit_info', kwargs={'article_id': article_id}))

    if request.POST and 'next_step' in request.POST:
        article.date_submitted = timezone.now()
        article.stage = models.STAGE_UNASSIGNED
        article.current_step = 5
        article.save()

        messages.add_message(request, messages.SUCCESS, 'Article {0} submitted'.format(article.title))

        kwargs = {'article': article,
                  'request': request}
        event_logic.Events.raise_event(event_logic.Events.ON_ARTICLE_SUBMITTED,
                                       task_object=article,
                                       **kwargs)

        return redirect(reverse('core_dashboard'))

    template = "submission/submit_review.html"
    context = {
        'article': article,
    }

    return render(request, template, context)


@production_user_or_editor_required
def edit_metadata(request, article_id):
    """
    Allows the Editors and Production Managers to edit an Article's metadata/
    :param request: request object
    :param article_id: PK of an Article
    :return: contextualised django template
    """
    article = get_object_or_404(models.Article, pk=article_id)
    info_form = forms.ArticleInfo(instance=article)
    frozen_author, modal = None, None
    return_param = request.GET.get('return')
    reverse_url = '{0}?return={1}'.format(reverse('edit_metadata', kwargs={'article_id': article.pk}), return_param)

    if request.GET.get('author'):
        frozen_author, modal = logic.get_author(request, article)
        author_form = forms.EditFrozenAuthor(instance=frozen_author)
    else:
        author_form = forms.EditFrozenAuthor()

    if request.POST:

        if 'metadata' in request.POST:
            info_form = forms.ArticleInfo(request.POST, instance=article)

            if info_form.is_valid():
                info_form.save()
                messages.add_message(request, messages.SUCCESS, 'Metadata updated.')
                return redirect(reverse_url)

        if 'author' in request.POST:
            author_form = forms.EditFrozenAuthor(request.POST, instance=frozen_author)

            if author_form.is_valid():
                saved_author = author_form.save()
                saved_author.article = article
                saved_author.save()
                messages.add_message(request, messages.SUCCESS, 'Author {0} updated.'.format(saved_author.full_name()))
                return redirect(reverse_url)

        if 'delete' in request.POST:
            frozen_author_id = request.POST.get('delete')
            frozen_author = get_object_or_404(models.FrozenAuthor,
                                              pk=frozen_author_id,
                                              article=article,
                                              article__journal=request.journal)
            frozen_author.delete()
            messages.add_message(request, messages.SUCCESS, 'Frozen Author deleted.')
            return redirect(reverse_url)

    template = 'submission/edit/metadata.html'
    context = {
        'article': article,
        'info_form': info_form,
        'author_form': author_form,
        'modal': modal,
        'frozen_author': frozen_author,
        'return': return_param
    }

    return render(request, template, context)


@production_user_or_editor_required
def order_authors(request, article_id):
    article = get_object_or_404(models.Article, pk=article_id, journal=request.journal)

    if request.POST:
        ids = [int(_id) for _id in request.POST.getlist('authors[]')]

        for author in article.frozenauthor_set.all():
            order = ids.index(author.pk)
            author.order = order
            author.save()

    return HttpResponse('Thanks')


@production_user_or_editor_required
def edit_identifiers(request, article_id, identifier_id=None, event=None):
    """
    View allows production and editor staff to update identifiers.
    :param request: request object
    :param article_id: PK of an Article
    :param identifier_id: PK of an Identifier
    :param event: String: delete
    :return: a contextualised django template
    """
    article = get_object_or_404(models.Article, pk=article_id)
    identifiers = identifier_models.Identifier.objects.filter(article=article)
    identifier, identifier_form, modal = None, forms.IdentifierForm(), None
    return_param = request.GET.get('return')
    reverse_url = '{0}?return={1}'.format(reverse('edit_identifiers', kwargs={'article_id': article.pk}), return_param)

    if identifier_id:
        identifier = get_object_or_404(identifier_models.Identifier, article=article, pk=identifier_id)
        identifier_form = forms.IdentifierForm(instance=identifier)
        modal = 'identifier'

        if event == 'delete':
            identifier.delete()
            messages.add_message(request, messages.WARNING, 'Identifier deleted.')
            return redirect(reverse_url)

    if request.POST:
        if 'issue_doi' in request.POST:
            # assuming there is only one DOI
            for identifier in identifiers:
                identifier.register()
        else:
            if identifier:
                identifier_form = forms.IdentifierForm(request.POST, instance=identifier)
            else:
                identifier_form = forms.IdentifierForm(request.POST)

            if identifier_form.is_valid():
                ident = identifier_form.save(commit=False)
                ident.article = article
                ident.save()

                return redirect(reverse_url)

    template = 'submission/edit/identifiers.html'
    context = {
        'article': article,
        'identifiers': identifiers,
        'identifier_form': identifier_form,
        'identifier': identifier,
        'modal': modal,
        'return': return_param,
    }

    return render(request, template, context)
