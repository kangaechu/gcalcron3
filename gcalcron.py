#!/usr/bin/env python3
#
#    gcalcron

"""Command-line synchronisation application between Google Calendar and Linux's Crontab.
Usage:
  $ python gcalcron3.py
"""

from __future__ import print_function
from __future__ import unicode_literals
from __future__ import division
from __future__ import absolute_import

import argparse
import httplib2
import os
import sys
import json
import datetime
import dateutil.parser
import subprocess
import re
import logging

# Google API
from builtins import open
from builtins import int
from builtins import str
from builtins import input
from builtins import object
from apiclient import discovery
from oauth2client import file
from oauth2client import client
from oauth2client import tools
from future import standard_library
from dateutil.tz import gettz

standard_library.install_aliases()

logger = logging.getLogger(__name__)

# Parser for command-line arguments.
parser = argparse.ArgumentParser(
    description=__doc__,
    formatter_class=argparse.RawDescriptionHelpFormatter,
    parents=[tools.argparser])
parser.add_argument('--reset', default=False,
                    help='Reset all synchronised events')


class GCalAdapter(object):
    """
    Adapter class which communicates with the Google Calendar API
    @since 2011-06-19
    """

    # CLIENT_SECRETS is name of a file containing the OAuth 2.0 information for this
    # application, including client_id and client_secret. You can see the Client ID
    # and Client secret on the APIs page in the Cloud Console:
    # <https://cloud.google.com/console#/project/395452703880/apiui>
    CLIENT_SECRETS = os.path.join(os.path.dirname(__file__), 'client_secrets.json')

    def __init__(self, calendar_id=None, flags=None):
        self.calendarId = calendar_id
        self.flags = flags
        self.service = None

    def get_service(self):
        if not self.service:
            # If the credentials don't exist or are invalid run through the native client
            # flow. The Storage object will ensure that if successful the good
            # credentials will get written back to the file.
            storage = file.Storage(os.path.join(os.path.dirname(__file__), 'credentials.dat'))
            credentials = storage.get()
            if credentials is None or credentials.invalid:
                # Set up a Flow object to be used for authentication.
                # Add one or more of the following scopes. PLEASE ONLY ADD THE SCOPES YOU
                # NEED. For more information on using scopes please see
                # <https://developers.google.com/+/best-practices>.
                flow = client.flow_from_clientsecrets(self.CLIENT_SECRETS,
                                                      scope=[
                                                          'https://www.googleapis.com/auth/calendar.readonly',
                                                      ],
                                                      message=tools.message_if_missing(self.CLIENT_SECRETS))

                credentials = tools.run_flow(flow, storage, self.flags)

            # Create an httplib2.Http object to handle our HTTP requests and authorize it
            # with our good Credentials.
            http = httplib2.Http()
            http = credentials.authorize(http)
            # Construct the service object for the interacting with the Calendar API.
            self.service = discovery.build('calendar', 'v3', http=http)

        return self.service

    def get_query(self, start_min, start_max, updated_min=None):
        """
        Builds the Google Calendar query with default options set

        >>> g = GCalAdapter()
        >>> g.get_query(datetime.datetime(2011, 6, 19, 14, 0),
        datetime.datetime(2011, 6, 26, 14, 0),
        datetime.datetime(2011, 6, 18, 14, 0))
        {'orderBy': 'updated', 'showDeleted': True, 'calendarId': None, 'timeMin': '2011-06-19T14:00:00',
        'updatedMin': '2011-06-18T14:00:00', 'timeMax': '2011-06-26T14:00:00',
        'fields': 'items(description,end,id,start,status,summary,updated)', 'singleEvents': True, 'maxResults': 1000}

        @author Fabrice Bernhard
        @since 2011-06-19
        """

        logger.info('Setting up query: %s to %s modified after %s' % (
            start_min.isoformat(), start_max.isoformat(), updated_min))

        query = {
            'calendarId': self.calendarId,
            'maxResults': 1000,
            'orderBy': 'updated',
            'singleEvents': True,
            'fields': 'items(description,end,id,location,start,status,summary,updated)',
            'timeMin': start_min.isoformat(),
            'timeMax': start_max.isoformat(),
        }

        if updated_min:
            query['updatedMin'] = updated_min.isoformat()
            # we want to know if planned events have been deleted since
            query['showDeleted'] = True

        return query

    def query_api(self, queries):
        """Query the Google Calendar API."""

        logger.info('Submitting query')

        entries = []
        for query in queries:
            try:
                page_token = None
                while True:
                    query['pageToken'] = page_token
                    g_cal_events = self.get_service().events().list(**query).execute()
                    entries += g_cal_events['items']
                    page_token = g_cal_events.get('nextPageToken')
                    if not page_token:
                        break
            except client.AccessTokenRefreshError:
                print("The credentials have been revoked or expired, please re-run"
                      "the application to re-authorize")

        logger.info('Query results received')
        logger.debug(entries)

        return entries

    def get_events(self, sync_start, last_sync=None, num_days=datetime.timedelta(days=7)):
        """
        Gets a list of events to sync
         - events between sync_start and last_sync + num_days which have been updated since last_sync
         - new events between last_sync + num_days and sync_start + num_days
        @author Fabrice Bernhard
        @since 2011-06-13
        """

        queries = []
        end = sync_start + num_days
        if last_sync:
            # query all events modified since last synchronisation
            queries.append(self.get_query(sync_start, last_sync + num_days, last_sync))
            # query all events which appeared in the [last_sync + num_days, sync_start + num_days] time frame
            queries.append(self.get_query(last_sync + num_days, end))
        else:
            queries.append(self.get_query(sync_start, end))

        return self.query_api(queries)


class GCalCron(object):
    """
    Schedule your cron commands in a dedicated Google Calendar,
    this class will convert them into UNIX "at" job list and keep
    them synchronised in case of updates

    @author Fabrice Bernhard
    @since 2011-06-13
    """

    settings = None
    settings_file = os.path.join(os.getcwd(), 'gcalcron_conf.json')

    def __init__(self, g_cal_adapter=None):
        self.gCalAdapter = g_cal_adapter
        self.load_settings()

    def load_settings(self):
        try:
            with open(self.settings_file) as f:
                self.settings = json.load(f)
        except IOError:
            calendar_id = input(
                'Calendar id (in the form of XXXXX....XXXX@group.calendar.google.com or for the main one just your '
                'Google email): ')
            self.init_settings(calendar_id)
            self.save_settings()

    def save_settings(self):
        with open(self.settings_file, 'w') as f:
            json.dump(self.settings, f, indent=2)

    def init_settings(self, calendar_id):
        self.settings = {
            "jobs": {},
            "calendarId": calendar_id,
            "last_sync": None
        }

    def get_calendar_id(self):
        return self.settings["calendarId"]

    def clean_settings(self):
        """Cleans the settings from saved jobs in the past"""

        for event_uid, job in list(self.settings['jobs'].items()):
            if datetime.datetime.strptime(job['date'], '%Y-%m-%d') <= datetime.datetime.now() - datetime.timedelta(
                    days=1):
                del self.settings['jobs'][event_uid]

    def reset_settings(self):
        for event, job in list(self.settings['jobs'].items()):
            command = [u'at', u'-d'] + job['ids']
            logger.debug(command)
            p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = p.communicate()
            logger.debug(stdout)
            logger.debug(stderr)
        self.settings['last_sync'] = None
        self.settings['jobs'] = {}
        self.save_settings()

    def unschedule_old_jobs(self, events):
        removed_job_ids = []
        for event in events:
            if event['uid'] in self.settings['jobs']:
                removed_job_ids += self.settings['jobs'][event['uid']]['ids']
                del self.settings['jobs'][event['uid']]
        if len(removed_job_ids) > 0:
            command = [u'at', u'-d'] + removed_job_ids
            logger.debug(command)
            p = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = p.communicate()
            logger.debug(stdout)
            logger.debug(stderr)

    def schedule_new_jobs(self, events):
        for event in events:
            if 'command' not in event:
                continue

            if event['command']['exec_time'] <= datetime.datetime.now():
                continue

            cmd = ['at', datetime_to_at(event['command']['exec_time'])]
            logger.debug(cmd)

            p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (stdout, stderr) = p.communicate(event['command']['command'].encode())

            logger.debug(stdout)
            logger.debug(stderr)

            job_id_match = re.compile('job (\d+) at').search(str(stderr))

            if job_id_match:
                job_id = job_id_match.group(1)
                logger.debug('identified job_id: ' + job_id)

                if event['uid'] in self.settings['jobs']:
                    self.settings['jobs'][event['uid']]['ids'].append(job_id)
                else:
                    self.settings['jobs'][event['uid']] = {
                        'date': event['command']['exec_time'].strftime('%Y-%m-%d'),
                        'ids': [job_id, ]
                    }

    def sync_gcal_to_cron(self, num_days=datetime.timedelta(days=7)):
        """
        - fetches a list of commands through the GoogleCalendar adapter
        - schedules them for execution using the unix "at" command
        - stores their job_id in case of later modifications
        - deletes eventual cancelled jobs

        @author Fabrice Bernhard
        @since 2011-06-13
        """

        last_sync = None
        if self.settings['last_sync']:
            last_sync = dateutil.parser.parse(self.settings['last_sync'])

        sync_start = datetime.datetime.now(gettz())
        events = self.gCalAdapter.get_events(sync_start, last_sync, num_days)
        command_list = parse_events(events)

        # first unschedule all modified/deleted events
        self.unschedule_old_jobs(command_list)

        # then reschedule all modified/new events
        self.schedule_new_jobs(command_list)

        # clean old jobs from the settings
        self.clean_settings()

        self.settings['last_sync'] = str(sync_start)
        self.save_settings()


def parse_command(event_description, start_time, end_time, event_summary, event_location):
    """
    Parses the description of a Google calendar event and returns a list of commands to execute

    >>> parse_command("echo 'Wake up!'\\n+10: echo 'Wake up, you are 10 minutes late!'",
    datetime.datetime(3011, 6, 19, 8, 30), datetime.datetime(3011, 6, 19, 9, 0))
    [{'exec_time': datetime.datetime(3011, 6, 19, 8, 30), 'command': "echo 'Wake up!'"},
    {'exec_time': datetime.datetime(3011, 6, 19, 8, 40), 'command': "echo 'Wake up, you are 10 minutes late!'"}]

    >>> parse_command("Turn on lights\\nend -10: Dim lights\\nend: Turn off lights",
    datetime.datetime(3011, 6, 19, 18, 30), datetime.datetime(3011, 6, 19, 23, 0))
    [{'exec_time': datetime.datetime(3011, 6, 19, 18, 30), 'command': 'Turn on lights'},
    {'exec_time': datetime.datetime(3011, 6, 19, 22, 50), 'command': 'Dim lights'},
    {'exec_time': datetime.datetime(3011, 6, 19, 23, 0), 'command': 'Turn off lights'}]


    @author Fabrice Bernhard
    @since 2011-06-13
    """
    command = os.path.abspath(os.path.join(os.path.dirname(__file__), 'gcalcron.sh'))
    exec_time = dateutil.parser.parse(start_time).replace(tzinfo=None)
    if exec_time >= datetime.datetime.now():
        command = {
            'command': command + ' "' + '" "'.join(
                [ start_time, end_time, event_summary, event_location, event_description]) + '"',
            'exec_time': exec_time
        }
        return command
    else:
        logger.debug('Ignoring command that was scheduled for the past')


def parse_events(events):
    """
    Transforms the Google Calendar API results into a list of commands

    >>> parse_events([{u'status': u'confirmed', u'updated': u'2013-12-22T19:49:13.750Z', u'end': {u'dateTime':
    u'2113-12-23T02:00:00+01:00'}, u'description': u'-60: start_heating.py\\n0: turn_music_on.py\\n+30:
    stop_heating.py', u'summary': u'Wakeup', u'start': {u'dateTime': u'2113-12-23T01:00:00+01:00'},
    u'id': u'olbia2urfm1ns0h88v4u0d9a5g'}]) [{'commands': [{'exec_time': datetime.datetime(2113, 12, 23, 0, 0),
    'command': u'start_heating.py'}, {'exec_time': datetime.datetime(2113, 12, 23, 1, 0), 'command': u'0:
    turn_music_on.py'}, {'exec_time': datetime.datetime(2113, 12, 23, 1, 30), 'command': u'stop_heating.py'}],
    'uid': u'olbia2urfm1ns0h88v4u0d9a5g'}]

    >>> parse_events([{u'status': u'cancelled', u'updated': u'2013-12-22T19:52:50.525Z',
     u'end': {u'dateTime': u'2013-12-23T02:00:00+01:00'},
     u'description': u'-60: start_heating.py\\n0: turn_music_on.py\\n+30: stop_heating.py', u'summary': u'Wakeup',
     u'start': {u'dateTime': u'2013-12-23T01:00:00+01:00'}, u'id': u'olbia2urfm1ns0h88v4u0d9a5g'}])
    [{'uid': u'olbia2urfm1ns0h88v4u0d9a5g'}]

    @author Fabrice Bernhard
    @since 2013-12-22
    """
    command_list = []
    for event in events:
        start_time = event['start']['dateTime']
        end_time = event['end']['dateTime']
        event_description = ''
        if 'description' in event:
            event_description = event['description']
        event_location = ''
        if 'location' in event:
            event_location = event['location']
        event_summary = ''
        if 'summary' in event:
            event_summary = event['summary']
        logger.debug(
            event['id'] + '-' + event['status'] + '-' + event['updated'] + ': ' + str(start_time) + ' -> ' + str(
                end_time) + ' (' + event['start']['dateTime'] + ' -> ' + event['end'][
                'dateTime'] + ') ' + '=>' + event_description)
        if event['status'] == 'cancelled':
            logger.info("cancelled " + event['id'])
            command_list.append({
                'uid': event['id']
            })
        elif event_description:
            command = parse_command(event_description, start_time, end_time, event_summary, event_location)
            if command:
                command_list.append({
                    'uid': event['id'],
                    'command': command
                })

    logger.debug(command_list)

    return command_list


def datetime_to_at(dt):
    """
    >>> datetime_to_at(datetime.datetime(2011, 6, 18, 12, 0))
    '12:00 Jun 18'
    """
    return dt.strftime('%H:%M %h %d')


def main(argv):
    # Parse the command-line flags.
    flags = parser.parse_args(argv[1:])

    level = getattr(logging, flags.logging_level)
    logger.setLevel(logging.DEBUG)
    h1 = logging.StreamHandler(sys.stdout)
    h1.setLevel(level)
    logger.addHandler(h1)

    fh = logging.FileHandler(os.path.join(os.path.dirname(__file__), 'gcalcron.log'))
    fh.setLevel(logging.DEBUG)
    logger.addHandler(fh)

    try:
        g = GCalCron()
        g_cal_adapter = GCalAdapter(g.get_calendar_id(), flags)
        g.gCalAdapter = g_cal_adapter

        if flags.reset:
            g.reset_settings()
        else:
            g.sync_gcal_to_cron()
            logger.info('Sync succeeded')
    except:
        logging.exception('Sync failed')


if __name__ == '__main__':
    main(sys.argv)
