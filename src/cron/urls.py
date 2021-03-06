__copyright__ = "Copyright 2017 Birkbeck, University of London"
__author__ = "Martin Paul Eve & Andy Byers"
__license__ = "AGPL v3"
__maintainer__ = "Birkbeck Centre for Technology and Publishing"
from django.conf.urls import url

from cron import views

urlpatterns = [
    url(r'^$', views.home, name='cron_home'),
    url(r'^reminders/$', views.reminders, name='cron_reminders'),
    url(r'^reminders/(?P<reminder_id>\d+)/$', views.reminder, name='cron_reminder'),
]
