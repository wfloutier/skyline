from __future__ import division
import redis
import logging
import simplejson as json
import sys
import re
import csv
import traceback
from msgpack import Unpacker
from functools import wraps
from flask import (
    Flask, request, render_template, redirect, Response, abort, flash,
    send_file, jsonify)
from daemon import runner
from os.path import isdir
from os import path
import string
from os import remove as os_remove
from time import sleep

# @added 20160703 - Feature #1464: Webapp Redis browser
import time
# @modified 20180918 - Feature #2602: Graphs in search_features_profiles
# from datetime import datetime, timedelta
from datetime import timedelta

import os
import base64

# flask things for rebrow
# @modified 20180918 - Feature #2602: Graphs in search_features_profiles
# from flask import session, g, url_for, flash, Markup, json
from flask import url_for, Markup

# For secret_key
import uuid

# @added 20170122 - Feature #1872: Ionosphere - features profile page by id only
# Determine the features profile dir path for a fp_id
import datetime
# from pytz import timezone
import pytz

# @added 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
# Added auth to rebrow as per https://github.com/marians/rebrow/pull/20 by
# elky84
from six.moves.urllib.parse import quote

# @modified 20180918 - Feature #2602: Graphs in search_features_profiles
# from features_profile import feature_name_id, calculate_features_profile
from features_profile import calculate_features_profile

from tsfresh_feature_names import TSFRESH_VERSION

# @modified 20180526 - Feature #2378: Add redis auth to Skyline and rebrow
# Use PyJWT instead of pycryptodome
# from Crypto.Cipher import AES
# import base64
# @added 20180526 - Feature #2378: Add redis auth to Skyline and rebrow
import jwt
# @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
import hashlib
from sys import version_info
from ast import literal_eval

# @added 20180721 - Feature #2464: luminosity_remote_data
# Use a gzipped response as the response as raw preprocessed time series
# added cStringIO, gzip and functools to implement Gzip for particular views
# http://flask.pocoo.org/snippets/122/
from flask import after_this_request
# from cStringIO import StringIO as IO
import gzip
import functools

from logging.handlers import TimedRotatingFileHandler, MemoryHandler

import os.path
# @added 20190116 - Cross-Site Scripting Security Vulnerability #85
#                   Bug #2816: Cross-Site Scripting Security Vulnerability
from flask import escape as flask_escape

sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))
import settings
from validate_settings import validate_settings_variables
import skyline_version
from skyline_functions import (
    get_graphite_metric,
    # @added 20170604 - Feature #2034: analyse_derivatives
    in_list,
    # @added 20180804 - Feature #2488: Allow user to specifically set metric as a derivative metric in training_data
    set_metric_as_derivative,
    # @added 20190510 - Feature #2990: Add metrics id to relevant web pages
    get_memcache_metric_object,
    # @added 20190920 - Feature #3230: users DB table
    #                   Ideas #2476: Label and relate anomalies
    #                   Feature #2516: Add label to features profile
    get_user_details,
)

from backend import (
    panorama_request, get_list,
    # @added 20180720 - Feature #2464: luminosity_remote_data
    luminosity_remote_data)
from ionosphere_backend import (
    ionosphere_data, ionosphere_metric_data,
    # @modified 20170114 - Feature #1854: Ionosphere learn
    # Decoupled create_features_profile from ionosphere_backend
    # ionosphere_get_metrics_dir, create_features_profile,
    ionosphere_get_metrics_dir,
    features_profile_details,
    # @added 20170118 - Feature #1862: Ionosphere features profiles search page
    ionosphere_search,
    # @added 20170305 - Feature #1960: ionosphere_layers
    create_ionosphere_layers, feature_profile_layers_detail,
    feature_profile_layer_alogrithms,
    # @added 20170308 - Feature #1960: ionosphere_layers
    # To present the operator with the existing layers and algorithms for the metric
    metric_layers_alogrithms,
    # @added 20170327 - Feature #2004: Ionosphere layers - edit_layers
    #                   Task #2002: Review and correct incorrectly defined layers
    edit_ionosphere_layers,
    # @added 20170402 - Feature #2000: Ionosphere - validated
    validate_fp,
    # @added 20170617 - Feature #2054: ionosphere.save.training_data
    save_training_data_dir,
    # added 20170908 - Feature #2056: ionosphere - disabled_features_profiles
    features_profile_family_tree, disable_features_profile_family_tree,
    # @added 20170916 - Feature #1996: Ionosphere - matches page
    get_fp_matches,
    # @added 20170917 - Feature #1996: Ionosphere - matches page
    get_matched_id_resources,
    # @added 20180812 - Feature #2430: Ionosphere validate learnt features profiles page
    get_features_profiles_to_validate,
    # @added 20180815 - Feature #2430: Ionosphere validate learnt features profiles page
    get_metrics_with_features_profiles_to_validate,
    # @added 20181205 - Bug #2746: webapp time out - Graphs in search_features_profiles
    #                   Feature #2602: Graphs in search_features_profiles
    ionosphere_show_graphs,
    # @added 20190502 - Branch #2646: slack
    webapp_update_slack_thread,
    # @added 20190601 - Feature #3084: Ionosphere - validated matches
    validate_ionosphere_match,
)

# from utilites import alerts_matcher

# @added 20170114 - Feature #1854: Ionosphere learn
# Decoupled the create_features_profile from ionosphere_backend and moved to
# ionosphere_functions so it can be used by ionosphere/learn
from ionosphere_functions import (
    create_features_profile, get_ionosphere_learn_details,
    # @added 20180414 - Branch #2270: luminosity
    get_correlations)

skyline_version = skyline_version.__absolute_version__

skyline_app = 'webapp'
skyline_app_logger = '%sLog' % skyline_app
logger = logging.getLogger(skyline_app_logger)
skyline_app_logfile = '%s/%s.log' % (settings.LOG_PATH, skyline_app)
skyline_app_loglock = '%s.lock' % skyline_app_logfile
skyline_app_logwait = '%s.wait' % skyline_app_logfile
logfile = '%s/%s.log' % (settings.LOG_PATH, skyline_app)

# werkzeug access log for Python errors
access_logger = logging.getLogger('werkzeug')

python_version = int(version_info[0])

# @added 20180721 - Feature #2464: luminosity_remote_data
# Use a gzipped response as the response as raw preprocessed time series
# added cStringIO to implement Gzip for particular views
# http://flask.pocoo.org/snippets/122/
if python_version == 2:
    from StringIO import StringIO as IO
if python_version == 3:
    import io as IO

# @modified 20180519 - Feature #2378: Add redis auth to Skyline and rebrow
if settings.REDIS_PASSWORD:
    REDIS_CONN = redis.StrictRedis(password=settings.REDIS_PASSWORD, unix_socket_path=settings.REDIS_SOCKET_PATH)
else:
    REDIS_CONN = redis.StrictRedis(unix_socket_path=settings.REDIS_SOCKET_PATH)

# ENABLE_WEBAPP_DEBUG = True

app = Flask(__name__)

# @modified 20190502 - Branch #2646: slack
# Reduce logging, removed gunicorn
# gunicorn_error_logger = logging.getLogger('gunicorn.error')
# logger.handlers.extend(gunicorn_error_logger.handlers)
# logger.setLevel(logging.DEBUG)

# app.secret_key = str(uuid.uuid5(uuid.NAMESPACE_DNS, settings.GRAPHITE_HOST))
secret_key = str(uuid.uuid5(uuid.NAMESPACE_DNS, settings.GRAPHITE_HOST))
app.secret_key = secret_key

app.config['PROPAGATE_EXCEPTIONS'] = True

app.config.update(
    SESSION_COOKIE_NAME='skyline',
    SESSION_COOKIE_SECURE=True,
    SECRET_KEY=secret_key
)

graph_url_string = str(settings.GRAPH_URL)
PANORAMA_GRAPH_URL = re.sub('\/render.*', '', graph_url_string)

# @added 20160727 - Bug #1524: Panorama dygraph not aligning correctly
# Defaults for momentjs to work if the setttings.py was not updated
try:
    WEBAPP_USER_TIMEZONE = settings.WEBAPP_USER_TIMEZONE
except:
    WEBAPP_USER_TIMEZONE = True
try:
    WEBAPP_FIXED_TIMEZONE = settings.WEBAPP_FIXED_TIMEZONE
except:
    WEBAPP_FIXED_TIMEZONE = 'Etc/GMT+0'
try:
    WEBAPP_JAVASCRIPT_DEBUG = settings.WEBAPP_JAVASCRIPT_DEBUG
except:
    WEBAPP_JAVASCRIPT_DEBUG = False

# @added 20190520 - Branch #3002: docker
try:
    GRAPHITE_RENDER_URI = settings.GRAPHITE_RENDER_URI
except:
    GRAPHITE_RENDER_URI = 'render'


@app.before_request
# def setup_logging():
#    if not app.debug:
#        stream_handler = logging.StreamHandler()
#        stream_handler.setLevel(logging.DEBUG)
#        app.logger.addHandler(stream_handler)
#        import logging
#        from logging.handlers import TimedRotatingFileHandler, MemoryHandler
#        file_handler = MemoryHandler(app_logfile, mode='a')
#        file_handler.setLevel(logging.DEBUG)
#        app.logger.addHandler(file_handler)
#
#        formatter = logging.Formatter("%(asctime)s :: %(process)s :: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
#        handler = logging.handlers.TimedRotatingFileHandler(
#            app_logfile,
#            when="midnight",
#            interval=1,
#            backupCount=5)
#        handler.setLevel(logging.DEBUG)
#
#        memory_handler = logging.handlers.MemoryHandler(100,
#                                                        flushLevel=logging.DEBUG,
#                                                        target=handler)
#        handler.setFormatter(formatter)
#        app.logger.addHandler(memory_handler)
#        app.logger.addHandler(handler)
def limit_remote_addr():
    """
    This function is called to check if the requesting IP address is in the
    settings.WEBAPP_ALLOWED_IPS array, if not 403.
    """
    ip_allowed = False
    for web_allowed_ip in settings.WEBAPP_ALLOWED_IPS:
        if request.remote_addr == web_allowed_ip:
            ip_allowed = True

    if not settings.WEBAPP_IP_RESTRICTED:
        ip_allowed = True

    if not ip_allowed:
        abort(403)  # Forbidden


def check_auth(username, password):
    """This function is called to check if a username /
    password combination is valid.
    """
    if settings.WEBAPP_AUTH_ENABLED:
        return username == settings.WEBAPP_AUTH_USER and password == settings.WEBAPP_AUTH_USER_PASSWORD
    else:
        return True


def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
        'Forbidden', 401,
        {'WWW-Authenticate': 'Basic realm="Login Required"'})


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if settings.WEBAPP_AUTH_ENABLED:
            auth = request.authorization
            if not auth or not check_auth(auth.username, auth.password):
                return authenticate()
            return f(*args, **kwargs)
        else:
            return True
    return decorated


# @added 20180721 - Feature #2464: luminosity_remote_data
# Use a gzipped response as the response as raw preprocessed time series with
# an implementation of Gzip for particular views
# http://flask.pocoo.org/snippets/122/
def gzipped(f):
    @functools.wraps(f)
    def view_func(*args, **kwargs):
        @after_this_request
        def zipper(response):
            accept_encoding = request.headers.get('Accept-Encoding', '')

            if 'gzip' not in accept_encoding.lower():
                return response

            response.direct_passthrough = False

            if (response.status_code < 200 or response.status_code >= 300 or 'Content-Encoding' in response.headers):
                return response
            gzip_buffer = IO()
            gzip_file = gzip.GzipFile(mode='wb',
                                      fileobj=gzip_buffer)
            gzip_file.write(response.data)
            gzip_file.close()

            response.data = gzip_buffer.getvalue()
            response.headers['Content-Encoding'] = 'gzip'
            response.headers['Vary'] = 'Accept-Encoding'
            response.headers['Content-Length'] = len(response.data)

            return response

        return f(*args, **kwargs)

    return view_func


@app.errorhandler(500)
def internal_error(message, traceback_format_exc):
    """
    Show traceback in the browser when running a flask app on a production
    server.
    By default, flask does not show any useful information when running on a
    production server.
    By adding this view, we output the Python traceback to the error 500 page
    and log.

    As per:
    Show flask traceback when running on production server
    https://gist.github.com/probonopd/8616a8ff05c8a75e4601 - Python traceback
    rendered nicely by Jinja2

    This can be tested by hitting SKYLINE_URL/a_500

    """
    fail_msg = str(message)
    trace = str(traceback_format_exc)
    logger.debug('debug :: returning 500 as there was an error, why else would 500 be returned...')
    logger.debug('debug :: sending the user that caused the error to happen, this useful information')
    logger.debug('debug :: but they or I may have already emailed it to you, you should check your inbox')
    logger.debug('%s' % str(traceback_format_exc))
    logger.debug('debug :: which is accompanied with the message')
    logger.debug('debug :: %s' % str(message))
    logger.debug('debug :: request url :: %s' % str(request.url))
    logger.debug('debug :: request referrer :: %s' % str(request.referrer))
    # resp = '<pre>%s</pre><pre>%s</pre>' % (str(message), str(traceback_format_exc))
#    return(resp), 500
    server_name = settings.SERVER_METRICS_NAME
    return render_template(
        'traceback.html', version=skyline_version,
        message=fail_msg, traceback=trace, bad_machine=server_name), 500


@app.route("/")
@requires_auth
def index():

    start = time.time()
    if 'uh_oh' in request.args:
        try:
            return render_template(
                'uh_oh.html', version=skyline_version,
                message="Testing uh_oh"), 200
        except:
            error_string = traceback.format_exc()
            logger.error('error :: failed to render uh_oh.html: %s' % str(error_string))
            return 'Uh oh ... a Skyline 500 :(', 500

    try:
        return render_template(
            'now.html', version=skyline_version,
            duration=(time.time() - start)), 200
    except:
        error_string = traceback.format_exc()
        logger.error('error :: failed to render index.html: %s' % str(error_string))
        return 'Uh oh ... a Skyline 500 :(', 500


@app.route("/a_500")
@requires_auth
def a_500():

    if 'message' in request.args:
        message = request.args.get(str('message'), None)
        logger.debug('debug :: message - %s' % str(message))
        test_500_string = message
    else:
        logger.debug('debug :: testing /a_500 route and app.errorhandler(500) internal_error function')
        message = 'Testing app.errorhandler(500) internal_error function, if you are seeing this it works - OK'
        test_500_string = 'This is a test to generate a ValueError and a HTTP response of 500 and display the traceback'

    try:
        test_errorhandler_500_internal_error = int(test_500_string)
        logger.debug(
            'debug :: test_errorhandler_500_internal_error tests OK with %s' % (
                str(test_errorhandler_500_internal_error)))
    except:
        trace = traceback.format_exc()
        logger.debug('debug :: test OK')
        return internal_error(message, trace)

    error_msg = 'failed test of /a_500 route and app.errorhandler(500) internal_error function'
    logger.error('error :: %s' % error_msg)
    resp = json.dumps({'results': error_msg})
    return resp, 501


@app.route("/now")
@requires_auth
def now():
    start = time.time()
    try:
        return render_template(
            'now.html', version=skyline_version, duration=(time.time() - start)), 200
    except:
        error_string = traceback.format_exc()
        logger.error('error :: failed to render now.html: %s' % str(error_string))
        return 'Uh oh ... a Skyline 500 :(', 500


@app.route("/then")
@requires_auth
def then():
    try:
        return render_template('then.html'), 200
    except:
        error_string = traceback.format_exc()
        logger.error('error :: failed to render then.html: %s' % str(error_string))
        return 'Uh oh ... a Skyline 500 :(', 500


@app.route("/anomalies.json")
def anomalies():
    try:
        anomalies_json = path.abspath(path.join(path.dirname(__file__), '..', settings.ANOMALY_DUMP))
        with open(anomalies_json, 'r') as f:
            json_data = f.read()
    except:
        logger.error('error :: failed to get anomalies.json: ' + traceback.format_exc())
        return 'Uh oh ... a Skyline 500 :(', 500
    return json_data, 200


@app.route("/panorama.json")
def panorama_anomalies():
    try:
        anomalies_json = path.abspath(path.join(path.dirname(__file__), '..', settings.ANOMALY_DUMP))
        panorama_json = string.replace(str(anomalies_json), 'anomalies.json', 'panorama.json')
        logger.info('opening - %s' % panorama_json)
        with open(panorama_json, 'r') as f:
            json_data = f.read()
    except:
        logger.error('error :: failed to get panorama.json: ' + traceback.format_exc())
        return 'Uh oh ... a Skyline 500 :(', 500
    return json_data, 200


@app.route("/app_settings")
@requires_auth
def app_settings():

    try:
        app_settings = {'GRAPH_URL': settings.GRAPH_URL,
                        'OCULUS_HOST': settings.OCULUS_HOST,
                        'FULL_NAMESPACE': settings.FULL_NAMESPACE,
                        'SKYLINE_VERSION': skyline_version,
                        'PANORAMA_ENABLED': settings.PANORAMA_ENABLED,
                        'PANORAMA_DATABASE': settings.PANORAMA_DATABASE,
                        'PANORAMA_DBHOST': settings.PANORAMA_DBHOST,
                        'PANORAMA_DBPORT': settings.PANORAMA_DBPORT,
                        'PANORAMA_DBUSER': settings.PANORAMA_DBUSER,
                        'PANORAMA_DBUSERPASS': 'redacted',
                        'PANORAMA_GRAPH_URL': PANORAMA_GRAPH_URL,
                        'WEBAPP_USER_TIMEZONE': settings.WEBAPP_USER_TIMEZONE,
                        'WEBAPP_FIXED_TIMEZONE': settings.WEBAPP_FIXED_TIMEZONE,
                        'WEBAPP_JAVASCRIPT_DEBUG': settings.WEBAPP_JAVASCRIPT_DEBUG
                        }
    except Exception as e:
        error = "error: " + e
        resp = json.dumps({'app_settings': error})
        return resp, 500

    resp = json.dumps(app_settings)
    return resp, 200


@app.route("/version")
@requires_auth
def version():

    try:
        version_settings = {'SKYLINE_VERSION': skyline_version}
        resp = json.dumps(version_settings)
        return resp, 200
    except:
        return "Not Found", 404


@app.route("/api", methods=['GET'])
# @modified 20180720 - Feature #2464: luminosity_remote_data
# Rnamed def from data to api for the purpose of trying to add some
# documentation relating to the API endpoints and their required parameters,
# the results or error and status codes they return and the context in which
# they are used.
# def data():
def api():

    # @added 20191008 - Feature #3252: webapp api - unique_metrics
    if 'unique_metrics' in request.args:
        try:
            unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
        except:
            logger.error(traceback.format_exc())
            logger.error('error :: Webapp could not get the unique_metrics list from Redis')
            return 'Internal Server Error', 500
        data_dict = {
  "status": {},
  "data": {
    "metrics": unique_metrics
  }
}
        return jsonify(data_dict), 200

    # @added 20180929
    if 'get_json' in request.args:
        source = None
        metric = None
        timestamp = None
        full_duration_data = False
        if 'source' in request.args:
            valid_source = False
            source = request.args.get('source', None)
            if source == 'features_profile' or source == 'training_data':
                valid_source = True
            if not valid_source:
                resp = json.dumps(
                    {'results': 'Error: an invalid source parameter was passed to /api?get_json valid sources are features_profile or training_data'})
                return resp, 400
        else:
            resp = json.dumps(
                {'results': 'Error: the required parameter source was not passed to /api?get_json - valid sources are features_profile or training_data'})
            return resp, 400
        if 'metric' in request.args:
            metric = request.args.get('metric', None)
        if not metric:
            resp = json.dumps(
                {'results': 'Error: no metric parameter was passed to /api?get_json'})
            return resp, 400
        if 'timestamp' in request.args:
            timestamp = request.args.get('timestamp', None)
        if not timestamp:
            resp = json.dumps(
                {'results': 'Error: no timestamp parameter was passed to /api?get_json'})
            return resp, 400
        if metric and timestamp:
            tuple_json_file = '%s.json' % metric
            if full_duration_data in request.args:
                full_duration_data = request.args.get('full_duration_data', None)
                if full_duration_data == 'true':
                    full_duration_in_hours = settings.FULL_DURATION / 60 / 60
                    tuple_json_file = '%s.mirage.redis.%sh.json' % (metric, str(full_duration_in_hours))
            metric_timeseries_dir = metric.replace('.', '/')
            if source == 'features_profile':
                source_file = '%s/%s/%s/%s' % (
                    settings.IONOSPHERE_PROFILES_FOLDER, metric_timeseries_dir,
                    str(timestamp), tuple_json_file)
            if source == 'training_data':
                source_file = '%s/%s/%s/%s' % (
                    settings.IONOSPHERE_DATA_FOLDER, str(timestamp),
                    metric_timeseries_dir, tuple_json_file)
            logger.info('converting tuple data for %s %s at %s to json' % (source, metric, timestamp))
            datapoints = None
            if not os.path.isfile(source_file):
                logger.error('error :: file not found - %s' % source_file)
                resp = json.dumps(
                    {'results': '404 data file not found'})
                return resp, 404
            with open(source_file) as f:
                for line in f:
                    datapoints = str(line).replace('(', '[').replace(')', ']')
            data_dict = {'metric': metric}
            datapoints = literal_eval(datapoints)
            data_dict['datapoints'] = datapoints
            return jsonify(data_dict), 200

    # @added 20180720 - Feature #2464: luminosity_remote_data
    # Added luminosity_remote_data endpoint, requires two request parameter:
    if 'luminosity_remote_data' in request.args:
        anomaly_timestamp = None
        if 'anomaly_timestamp' in request.args:
            anomaly_timestamp_str = request.args.get(str('anomaly_timestamp'), None)
            try:
                anomaly_timestamp = int(anomaly_timestamp_str)
            except:
                anomaly_timestamp = None
        else:
            resp = json.dumps(
                {'results': 'Error: no anomaly_timestamp parameter was passed to /api?luminosity_remote_data'})
            return resp, 400
        luminosity_data = []
        if anomaly_timestamp:
            luminosity_data, success, message = luminosity_remote_data(anomaly_timestamp)
            if luminosity_data:
                resp = json.dumps(
                    {'results': luminosity_data})
                return resp, 200
            else:
                resp = json.dumps(
                    {'results': 'No data found'})
                return resp, 404

    if 'metric' in request.args:
        metric = request.args.get(str('metric'), None)
        try:
            raw_series = REDIS_CONN.get(metric)
            if not raw_series:
                resp = json.dumps(
                    {'results': 'Error: No metric by that name - try /api?metric=' + settings.FULL_NAMESPACE + 'metric_namespace'})
                return resp, 404
            else:
                unpacker = Unpacker(use_list=False)
                unpacker.feed(raw_series)
                timeseries = [item[:2] for item in unpacker]
                resp = json.dumps({'results': timeseries})
                return resp, 200
        except Exception as e:
            error = "Error: " + e
            resp = json.dumps({'results': error})
            return resp, 500

    if 'graphite_metric' in request.args:
        logger.info('processing graphite_metric api request')
        for i in request.args:
            key = str(i)
            value = request.args.get(key, None)
            logger.info('request argument - %s=%s' % (key, str(value)))

        valid_request = True
        missing_arguments = []

        metric = request.args.get('graphite_metric', None)
        from_timestamp = request.args.get('from_timestamp', None)
        until_timestamp = request.args.get('until_timestamp', None)

        if not metric:
            valid_request = False
            missing_arguments.append('graphite_metric')
            logger.error('graphite_metric argument not found')
        else:
            logger.info('graphite_metric - %s' % metric)

        if not from_timestamp:
            valid_request = False
            missing_arguments.append('from_timestamp')
            logger.error('from_timestamp argument not found')
        else:
            logger.info('from_timestamp - %s' % str(from_timestamp))

        if not until_timestamp:
            valid_request = False
            missing_arguments.append('until_timestamp')
        else:
            logger.info('until_timestamp - %s' % str(until_timestamp))

        if not valid_request:
            error = 'Error: not all arguments where passed, missing %s' % str(missing_arguments)
            resp = json.dumps({'results': error})
            return resp, 404
        else:
            logger.info('requesting data from graphite for %s from %s to %s' % (
                str(metric), str(from_timestamp), str(until_timestamp)))

        try:
            timeseries = get_graphite_metric(
                skyline_app, metric, from_timestamp, until_timestamp, 'json',
                'object')
        except:
            error = 'error :: %s' % str(traceback.print_exc())
            resp = json.dumps({'results': error})
            return resp, 500

        resp = json.dumps({'results': timeseries})
        cleaned_resp = False
        try:
            format_resp_1 = string.replace(str(resp), '"[[', '[[')
            cleaned_resp = string.replace(str(format_resp_1), ']]"', ']]')
        except:
            logger.error('error :: failed string replace resp: ' + traceback.format_exc())

        if cleaned_resp:
            return cleaned_resp, 200
        else:
            resp = json.dumps(
                {'results': 'Error: failed to generate timeseries'})
            return resp, 404

    resp = json.dumps(
        {'results': 'Error: No argument passed - try /api?metric= or /api?graphite_metric='})
    return resp, 404


# @added 20180721 - Feature #2464: luminosity_remote_data
# Add a specific route for the luminosity_remote_data endpoint so that the
# response can be gzipped as even the preprocessed data can run into megabyte
# reponses.
@app.route("/luminosity_remote_data", methods=['GET'])
@gzipped
def luminosity_remote_data_endpoint():
    # The luminosity_remote_data_endpoint, requires onerequest parameter:
    if 'anomaly_timestamp' in request.args:
        anomaly_timestamp_str = request.args.get(str('anomaly_timestamp'), None)
        try:
            anomaly_timestamp = int(anomaly_timestamp_str)
        except:
            anomaly_timestamp = None
    else:
        resp = json.dumps(
            {'results': 'Error: no anomaly_timestamp parameter was passed to /luminosity_remote_data'})
        return resp, 400
    luminosity_data = []
    if anomaly_timestamp:
        luminosity_data, success, message = luminosity_remote_data(anomaly_timestamp)
        if luminosity_data:
            resp = json.dumps(
                {'results': luminosity_data})
            logger.info('returning gzipped response')
            return resp, 200
        else:
            resp = json.dumps(
                {'results': 'No data found'})
            return resp, 404


@app.route("/docs")
@requires_auth
def docs():
    start = time.time()
    try:
        return render_template(
            'docs.html', version=skyline_version, duration=(time.time() - start)), 200
    except:
        return 'Uh oh ... a Skyline 500 :(', 500


@app.route("/panorama", methods=['GET'])
@requires_auth
def panorama():
    if not settings.PANORAMA_ENABLED:
        try:
            return render_template(
                'uh_oh.html', version=skyline_version,
                message="Panorama is not enabled, please see the Panorama section in the docs and settings.py"), 200
        except:
            return 'Uh oh ... a Skyline 500 :(', 500

    start = time.time()

    try:
        apps = get_list('app')
    except:
        logger.error('error :: %s' % traceback.print_exc())
        apps = ['None']
    try:
        sources = get_list('source')
    except:
        logger.error('error :: %s' % traceback.print_exc())
        sources = ['None']
    try:
        algorithms = get_list('algorithm')
    except:
        logger.error('error :: %s' % traceback.print_exc())
        algorithms = ['None']
    try:
        hosts = get_list('host')
    except:
        logger.error('error :: %s' % traceback.print_exc())
        hosts = ['None']

    request_args_present = False
    try:
        request_args_len = len(request.args)
        request_args_present = True
    except:
        request_args_len = 0

    # @added 20160803 - Sanitize request.args
    REQUEST_ARGS = ['from_date',
                    'from_time',
                    'from_timestamp',
                    'until_date',
                    'until_time',
                    'until_timestamp',
                    'count_by_metric',
                    'metric',
                    'metric_like',
                    'app',
                    'source',
                    'host',
                    'algorithm',
                    'limit',
                    'order',
                    # @added 20161127 - Branch #922: ionosphere
                    'panorama_anomaly_id',
                    ]

    # @added 20190919 - Feature #3230: users DB table
    #                   Ideas #2476: Label and relate anomalies
    #                   Feature #2516: Add label to features profile
    user_id = None
    if settings.WEBAPP_AUTH_ENABLED:
        auth = request.authorization
        user = auth.username
    else:
        user = 'Skyline'
        user_id = 1
    if not user_id:
        success, user_id = get_user_details(skyline_app, 'id', 'username', str(user))
        if not success:
            logger.error('error : /panorama could not get_user_details(%s)' % str(user))
            return 'Internal Server Error - ref: i - could not determine user_id', 500
        else:
            try:
                user_id = int(user_id)
            except:
                logger.error(traceback.format_exc())
                logger.error('error :: /panorama get_user_details(%s) did not return an int' % (
                    str(user), str(user_id)))
                return 'Internal Server Error - ref: p - user_id not int', 500

    get_anomaly_id = False
    if request_args_present:
        for i in request.args:
            key = str(i)
            if key not in REQUEST_ARGS:
                logger.error('error :: invalid request argument - %s=%s' % (key, str(i)))
                # @modified 20190524 - Branch #3002: docker
                # Return data
                # return 'Bad Request', 400
                error_string = 'error :: invalid request argument - %s=%s' % (key, str(i))
                logger.error(error_string)
                resp = json.dumps(
                    {'400 Bad Request': error_string})
                return flask_escape(resp), 400

            value = request.args.get(key, None)
            logger.info('request argument - %s=%s' % (key, str(value)))

            # @added 20161127 - Branch #922: ionosphere
            if key == 'panorama_anomaly_id':
                if str(value) == 'true':
                    get_anomaly_id = True

            if key == 'metric' and value != 'all':
                if value != '':
                    try:
                        unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                    except:
                        logger.error('error :: Webapp could not get the unique_metrics list from Redis')
                        logger.info(traceback.format_exc())
                        return 'Internal Server Error', 500
                    metric_name = settings.FULL_NAMESPACE + value

                    # @added 20180423 - Feature #2034: analyse_derivatives
                    #                   Branch #2270: luminosity
                    other_unique_metrics = []
                    # @added 20190105 - Bug #2792: webapp 500 error on no metric
                    # This needs to be set before the below conditional check
                    # otherwise webapp return a 500 server error instead of a
                    # 404 if the metric does not exist
                    metric_found_in_other_redis = False

                    if metric_name not in unique_metrics and settings.OTHER_SKYLINE_REDIS_INSTANCES:
                        metric_found_in_other_redis = False

                        # @modified 20180519 - Feature #2378: Add redis auth to Skyline and rebrow
                        # for redis_ip, redis_port in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                        for redis_ip, redis_port, redis_password in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                            if not metric_found_in_other_redis:
                                try:
                                    if redis_password:
                                        other_redis_conn = redis.StrictRedis(host=str(redis_ip), port=int(redis_port), password=str(redis_password))
                                    else:
                                        other_redis_conn = redis.StrictRedis(host=str(redis_ip), port=int(redis_port))
                                    other_unique_metrics = list(other_redis_conn.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                                except:
                                    logger.error(traceback.format_exc())
                                    logger.error('error :: failed to connect to Redis at %s on port %s' % (str(redis_ip), str(redis_port)))
                                if metric_name in other_unique_metrics:
                                    metric_found_in_other_redis = True
                                    logger.info('%s found in derivative_metrics in Redis at %s on port %s' % (metric_name, str(redis_ip), str(redis_port)))

                    if metric_name not in unique_metrics and not metric_found_in_other_redis:
                        error_string = 'error :: no metric - %s - exists in Redis' % metric_name
                        logger.error(error_string)
                        resp = json.dumps(
                            {'404 Not Found': error_string})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404

            if key == 'count_by_metric':
                count_by_metric_invalid = True
                if value == 'false':
                    count_by_metric_invalid = False
                if value == 'true':
                    count_by_metric_invalid = False
                if count_by_metric_invalid:
                    error_string = 'error :: invalid %s value passed %s' % (key, str(value))
                    logger.error(error_string)
                    # @modified 20190524 - Branch #3002: docker
                    # Return data
                    # return 'Bad Request', 400
                    resp = json.dumps(
                        {'400 Bad Request': error_string})
                    return flask_escape(resp), 400

            if key == 'metric_like':
                if value == 'all':
                    metric_namespace_pattern = value.replace('all', '')

                metric_namespace_pattern = value.replace('%', '')
                if metric_namespace_pattern != '' and value != 'all':
                    try:
                        unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                    except:
                        logger.error('error :: Webapp could not get the unique_metrics list from Redis')
                        logger.info(traceback.format_exc())
                        return 'Internal Server Error', 500

                    matching = [s for s in unique_metrics if metric_namespace_pattern in s]
                    if len(matching) == 0:
                        error_string = 'error :: no metric like - %s - exists in Redis' % metric_namespace_pattern
                        logger.error(error_string)
                        resp = json.dumps(
                            {'results': error_string})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404

            if key == 'from_timestamp' or key == 'until_timestamp':
                timestamp_format_invalid = True
                if value == 'all':
                    timestamp_format_invalid = False
                # unix timestamp
                if value.isdigit():
                    timestamp_format_invalid = False
                # %Y%m%d %H:%M timestamp
                if timestamp_format_invalid:
                    value_strip_colon = value.replace(':', '')
                    new_value = value_strip_colon.replace(' ', '')
                    if new_value.isdigit():
                        timestamp_format_invalid = False
                if timestamp_format_invalid:
                    error_string = 'error :: invalid %s value passed %s' % (key, value)
                    logger.error('error :: invalid %s value passed %s' % (key, value))
                    # @modified 20190524 - Branch #3002: docker
                    # Return data
                    # return 'Bad Request', 400
                    resp = json.dumps(
                        {'400 Bad Request': error_string})
                    return flask_escape(resp), 400

            if key == 'app':
                if value != 'all':
                    if value not in apps:
                        error_string = 'error :: no %s - %s' % (key, value)
                        logger.error(error_string)
                        resp = json.dumps(
                            {'results': error_string})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404

            if key == 'source':
                if value != 'all':
                    if value not in sources:
                        error_string = 'error :: no %s - %s' % (key, value)
                        logger.error(error_string)
                        resp = json.dumps(
                            {'results': error_string})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404

            if key == 'algorithm':
                if value != 'all':
                    if value not in algorithms:
                        error_string = 'error :: no %s - %s' % (key, value)
                        logger.error(error_string)
                        resp = json.dumps(
                            {'results': error_string})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404

            if key == 'host':
                if value != 'all':
                    if value not in hosts:
                        error_string = 'error :: no %s - %s' % (key, value)
                        logger.error(error_string)
                        resp = json.dumps(
                            {'results': error_string})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404

            if key == 'limit':
                limit_invalid = True
                limit_is_not_numeric = True
                if value.isdigit():
                    limit_is_not_numeric = False

                if limit_is_not_numeric:
                    error_string = 'error :: %s must be a numeric value - requested %s' % (key, value)
                    logger.error(error_string)
                    resp = json.dumps(
                        {'results': error_string})
                    # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                    #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                    # return resp, 400
                    return flask_escape(resp), 400

                new_value = int(value)
                try:
                    valid_value = new_value + 1
                except:
                    valid_value = None
                if valid_value and new_value < 101:
                    limit_invalid = False
                if limit_invalid:
                    error_string = 'error :: %s must be < 100 - requested %s' % (key, value)
                    logger.error(error_string)
                    resp = json.dumps(
                        {'results': error_string})
                    # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                    #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                    # return resp, 400
                    return flask_escape(resp), 400

            if key == 'order':
                order_invalid = True
                if value == 'DESC':
                    order_invalid = False
                if value == 'ASC':
                    order_invalid = False
                if order_invalid:
                    error_string = 'error :: %s must be DESC or ASC' % (key)
                    logger.error(error_string)
                    resp = json.dumps(
                        {'results': error_string})
                    # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                    #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                    # return resp, 400
                    return flask_escape(resp), 400

    # @added 20161127 - Branch #922: ionosphere
    if get_anomaly_id:
        try:
            query, panorama_data = panorama_request()
            logger.info('debug :: panorama_data - %s' % str(panorama_data))
        except:
            logger.error('error :: failed to get panorama: ' + traceback.format_exc())
            return 'Uh oh ... a Skyline 500 :(', 500

        try:
            duration = (time.time() - start)
            logger.info('debug :: duration - %s' % str(duration))
            resp = str(panorama_data)
            return resp, 200
        except:
            logger.error('error :: failed to render panorama.html: ' + traceback.format_exc())
            return 'Uh oh ... a Skyline 500 :(', 500

    if request_args_len == 0:
        try:
            panorama_data = panorama_request()
            # logger.info('panorama_data - %s' % str(panorama_data))
            return render_template(
                'panorama.html', anomalies=panorama_data, app_list=apps,
                source_list=sources, algorithm_list=algorithms,
                host_list=hosts, results='Latest anomalies',
                version=skyline_version, duration=(time.time() - start)), 200
        except:
            logger.error('error :: failed to get panorama: ' + traceback.format_exc())
            return 'Uh oh ... a Skyline 500 :(', 500
    else:
        count_request = 'false'
        if 'count_by_metric' in request.args:
            count_by_metric = request.args.get('count_by_metric', None)
            if count_by_metric == 'true':
                count_request = 'true'
        try:
            query, panorama_data = panorama_request()
            try:
                if settings.ENABLE_DEBUG or settings.ENABLE_WEBAPP_DEBUG:
                    logger.info('panorama_data - %s' % str(panorama_data))
                    logger.info('debug :: query - %s' % str(query))
                    logger.info('debug :: panorama_data - %s' % str(panorama_data))
                    logger.info('debug :: skyline_version - %s' % str(skyline_version))
            except:
                logger.error('error :: ENABLE_DEBUG or ENABLE_WEBAPP_DEBUG are not set in settings.py')
        except:
            logger.error('error :: failed to get panorama_request: ' + traceback.format_exc())
            return 'Uh oh ... a Skyline 500 :(', 500

        try:
            results_string = 'Found anomalies for %s' % str(query)

            duration = (time.time() - start)
            logger.info('debug :: duration - %s' % str(duration))
            return render_template(
                'panorama.html', anomalies=panorama_data, app_list=apps,
                source_list=sources, algorithm_list=algorithms,
                host_list=hosts, results=results_string, count_request=count_request,
                version=skyline_version, duration=(time.time() - start)), 200
        except:
            logger.error('error :: failed to render panorama.html: ' + traceback.format_exc())
            return 'Uh oh ... a Skyline 500 :(', 500


# Feature #1448: Crucible web UI - @earthgecko
# Branch #868: crucible - @earthgecko
# This may actually need Django, perhaps this is starting to move outside the
# realms of Flask..
@app.route("/crucible", methods=['GET'])
@requires_auth
def crucible():

    crucible_web_ui_implemented = False
    if crucible_web_ui_implemented:
        try:
            return render_template(
                'uh_oh.html', version=skyline_version,
                message="Sorry the Crucible web UI is not completed yet"), 200
        except:
            return render_template(
                'uh_oh.html', version=skyline_version,
                message="Sorry the Crucible web UI is not completed yet"), 200

# @added 20161123 - Branch #922: ionosphere


@app.route("/ionosphere", methods=['GET'])
@requires_auth
def ionosphere():
    if not settings.IONOSPHERE_ENABLED:
        try:
            return render_template(
                'uh_oh.html', version=skyline_version,
                message="Ionosphere is not enabled, please see the Ionosphere section in the docs and settings.py"), 200
        except:
            return 'Uh oh ... a Skyline 500 :(', 500

    start = time.time()

    # @added 20190919 - Feature #3230: users DB table
    #                   Ideas #2476: Label and relate anomalies
    #                   Feature #2516: Add label to features profile
    user_id = None
    if settings.WEBAPP_AUTH_ENABLED:
        auth = request.authorization
        user = auth.username
    else:
        user = 'Skyline'
        user_id = 1
    if not user_id:
        success, user_id = get_user_details(skyline_app, 'id', 'username', str(user))
        if not success:
            logger.error('error :: /ionosphere could not get_user_details(%s)' % str(user))
            return 'Internal Server Error - ref: i - could not determine user_id', 500
        else:
            try:
                user_id = int(user_id)
                logger.info('/ionosphere get_user_details() with %s returned user id %s' % (
                    str(user), str(user_id)))
            except:
                logger.error(traceback.format_exc())
                logger.error('error :: /ionosphere get_user_details() with %s did not return an int' % (
                    str(user), str(user_id)))
                return 'Internal Server Error - ref: i - user_id not int', 500

    request_args_present = False
    try:
        request_args_len = len(request.args)
        request_args_present = True
    except:
        request_args_len = 0

    if request_args_len:
        for i in request.args:
            key = str(i)
            value = request.args.get(key, None)
            logger.info('request argument - %s=%s' % (key, str(value)))

    # @added 20180419 - Feature #1996: Ionosphere - matches page
    #                   Branch #2270: luminosity
    # Change the default search parameters to return all matches for the
    # past 24 hours
    matched_request_timestamp = int(time.time())
    default_matched_from_timestamp = matched_request_timestamp - 86400
    matched_from_datetime = time.strftime('%Y%m%d %H:%M', time.localtime(default_matched_from_timestamp))

    # @added 20190328 - Feature #2484: FULL_DURATION feature profiles
    # Added ionosphere_echo
    echo_hdate = False

    metric_id = False

    # @added 20180812 - Feature #2430: Ionosphere validate learnt features profiles page
    features_profiles_to_validate = []
    fp_validate_req = False
    if 'fp_validate' in request.args:
        fp_validate_req = request.args.get(str('fp_validate'), None)
        if fp_validate_req == 'true':
            fp_validate_req = True
    if fp_validate_req:
        metric_found = False
        # @modified 20190503 - Branch #2646: slack - linting
        # timestamp = False
        base_name = False
        # @added 20181013 - Feature #2430: Ionosphere validate learnt features profiles page
        # Added the validate_all context and function
        validate_all = False
        all_validated = False
        metric_id = False
        validated_count = 0

        for i in request.args:
            key = str(i)
            value = request.args.get(key, None)
            logger.info('request argument - %s=%s' % (key, str(value)))

            # @added 20181013 - Feature #2430: Ionosphere validate learnt features profiles page
            # Added the validate_all context and function
            if key == 'validate_all':
                if str(value) == 'true':
                    validate_all = True
            if key == 'all_validated':
                if str(value) == 'true':
                    all_validated = True
                    # Ensure that validate_all is set to False so another call
                    # is not made to the validate_all function
                    validate_all = False
            if key == 'metric_id':
                try:
                    if isinstance(int(value), int):
                        metric_id = int(value)
                except:
                    logger.error('error :: the metric_id request parameter was passed but is not an int - %s' % str(value))
            if key == 'validated_count':
                try:
                    if isinstance(int(value), int):
                        validated_count = int(value)
                except:
                    logger.error('error :: the validated_count request parameter was passed but is not an int - %s' % str(value))

            if key == 'order':
                order = str(value)
                if order == 'DESC':
                    ordered_by = 'DESC'
                if order == 'ASC':
                    ordered_by = 'ASC'
            if key == 'limit':
                limit = str(value)
                try:
                    test_limit = int(limit) + 0
                    limited_by = test_limit
                except:
                    logger.error('error :: limit is not an integer - %s' % str(limit))
                    limited_by = '30'
            if key == 'metric':
                base_name = str(value)
                if base_name == 'all':
                    metric_found = True
                    metric_name = 'all'
                    limited_by = 0
                    ordered_by = 'DESC'
                if not metric_found:
                    metric_name = settings.FULL_NAMESPACE + base_name
                    try:
                        unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                    except:
                        logger.error(traceback.format_exc())
                        logger.error('error :: Webapp could not get the unique_metrics list from Redis')
                        return 'Internal Server Error', 500
                    if metric_name in unique_metrics:
                        metric_found = True
                    # @added 20180423 - Feature #2034: analyse_derivatives
                    #                   Branch #2270: luminosity
                    other_unique_metrics = []
                    if metric_name not in unique_metrics and settings.OTHER_SKYLINE_REDIS_INSTANCES:
                        metric_found_in_other_redis = False
                        # @modified 20180519 - Feature #2378: Add redis auth to Skyline and rebrow
                        # for redis_ip, redis_port in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                        for redis_ip, redis_port, redis_password in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                            if not metric_found_in_other_redis:
                                try:
                                    if redis_password:
                                        other_redis_conn = redis.StrictRedis(host=str(redis_ip), port=int(redis_port), password=str(redis_password))
                                    else:
                                        other_redis_conn = redis.StrictRedis(host=str(redis_ip), port=int(redis_port))
                                    other_unique_metrics = list(other_redis_conn.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                                except:
                                    logger.error(traceback.format_exc())
                                    logger.error('error :: failed to connect to Redis at %s on port %s' % (str(redis_ip), str(redis_port)))
                                if metric_name in other_unique_metrics:
                                    metric_found_in_other_redis = True
                                    metric_found = True
                                    logger.info('%s found in derivative_metrics in Redis at %s on port %s' % (metric_name, str(redis_ip), str(redis_port)))
        if metric_found:

            # @added 20181013 - Feature #2430: Ionosphere validate learnt features profiles page
            # Added the validate_all context and function, first do the
            # validate_all if it has been passed
            if validate_all and metric_id:
                features_profiles_to_validate_count = 0
                try:
                    features_profiles_to_validate, fail_msg, trace = get_features_profiles_to_validate(base_name)
                    features_profiles_to_validate_count = len(features_profiles_to_validate)
                    logger.info('%s features profiles found that need validating for %s with metric_id %s' % (
                        str(features_profiles_to_validate_count), base_name, str(metric_id)))
                except:
                    trace = traceback.format_exc()
                    message = 'Uh oh ... a Skyline 500 using get_features_profiles_to_validate(%s) with metric_id %s' % (str(base_name), str(metric_id))
                    return internal_error(message, trace)
                if features_profiles_to_validate_count > 0:
                    try:
                        # @modified 20190919 - Feature #3230: users DB table
                        #                      Ideas #2476: Label and relate anomalies
                        #                      Feature #2516: Add label to features profile
                        # Added user_id
                        # all_validated, fail_msg, traceback_format_exc = validate_fp(int(metric_id), 'metric_id')
                        all_validated, fail_msg, traceback_format_exc = validate_fp(int(metric_id), 'metric_id', user_id)
                        logger.info('validated all the enabled, unvalidated features profiles for metric_id - %s' % str(metric_id))
                        if all_validated:
                            validated_count = features_profiles_to_validate_count
                    except:
                        trace = traceback.format_exc()
                        message = 'Uh oh ... a Skyline 500 using get_features_profiles_to_validate(%s) with metric_id %s' % (str(base_name), str(metric_id))
                        return internal_error(message, trace)

            features_profiles_to_validate = []
            if metric_name != 'all':
                try:
                    features_profiles_to_validate, fail_msg, trace = get_features_profiles_to_validate(base_name)
                    # features_profiles_to_validate
                    # [ fp_id, metric_id, metric, full_duration, anomaly_timestamp,
                    #   fp_parent_id, parent_full_duration, parent_anomaly_timestamp,
                    #   fp_date, fp_graph_uri, parent_fp_date, parent_fp_graph_uri,
                    #   parent_prent_fp_id, fp_learn_graph_uri, parent_fp_learn_graph_uri,
                    #   minimum_full_duration, maximum_full_duration]
                    logger.info('%s features profiles found that need validating for %s' % (
                        str(len(features_profiles_to_validate)), base_name))
                except:
                    trace = traceback.format_exc()
                    message = 'Uh oh ... a Skyline 500 using get_features_profiles_to_validate(%s)' % str(base_name)
                    return internal_error(message, trace)
            try:
                default_learn_full_duration = int(settings.IONOSPHERE_LEARN_DEFAULT_FULL_DURATION_DAYS) * 24 * 60 * 60
            except:
                default_learn_full_duration = 30 * 24 * 60 * 60
            metrics_with_features_profiles_to_validate = []
            if not features_profiles_to_validate:
                # metrics_with_features_profiles_to_validate
                # [[metric_id, metric, fps_to_validate_count]]
                metrics_with_features_profiles_to_validate, fail_msg, trace = get_metrics_with_features_profiles_to_validate()

                # @added 20190501 - Feature #2430: Ionosphere validate learnt features profiles page
                # Only add to features_profiles_to_validate if the metric is active
                # in Redis
                if not settings.OTHER_SKYLINE_REDIS_INSTANCES:
                    if metrics_with_features_profiles_to_validate:
                        active_metrics_with_features_profiles_to_validate = []
                        try:
                            unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                        except:
                            logger.error('error :: Webapp could not get the unique_metrics list from Redis')
                            logger.info(traceback.format_exc())
                            return 'Internal Server Error', 500
                        for i_metric_id, i_metric, fps_to_validate_count in metrics_with_features_profiles_to_validate:
                            i_metric_name = '%s%s' % (settings.FULL_NAMESPACE, str(i_metric))
                            if i_metric_name not in unique_metrics:
                                continue
                            active_metrics_with_features_profiles_to_validate.append([i_metric_id, i_metric, fps_to_validate_count])
                        metrics_with_features_profiles_to_validate = active_metrics_with_features_profiles_to_validate

                logger.info('no features_profiles_to_validate was passed so determined metrics_with_features_profiles_to_validate')

            # @added 20190503 - Branch #2646: slack
            if validated_count > 0:
                slack_updated = webapp_update_slack_thread(base_name, 0, validated_count, 'validated')
                logger.info('slack_updated for validated features profiles %s' % str(slack_updated))

            return render_template(
                'ionosphere.html', fp_validate=fp_validate_req,
                features_profiles_to_validate=features_profiles_to_validate,
                metrics_with_features_profiles_to_validate=metrics_with_features_profiles_to_validate,
                for_metric=base_name, order=ordered_by, limit=limited_by,
                default_learn_full_duration=default_learn_full_duration,
                matched_from_datetime=matched_from_datetime,
                validate_all=validate_all, all_validated=all_validated,
                validated_count=validated_count,
                version=skyline_version,
                # @added 20190919 - Feature #3230: users DB table
                #                   Feature #2516: Add label to features profile
                user=user,
                duration=(time.time() - start), print_debug=False), 200

    # @added 20170220 - Feature #1862: Ionosphere features profiles search page
    # Ionosphere features profiles by generations
    fp_search_req = None
    # @added 20170916 - Feature #1996: Ionosphere - matches page
    # Handle both fp_search and fp_matches
    fp_search_or_matches_req = False

    if 'fp_search' in request.args:
        fp_search_req = request.args.get(str('fp_search'), None)
        if fp_search_req == 'true':
            fp_search_req = True
            fp_search_or_matches_req = True
        else:
            fp_search_req = False

    # @added 20170916 - Feature #1996: Ionosphere - matches page
    fp_matches_req = None
    if 'fp_matches' in request.args:
        fp_matches_req = request.args.get(str('fp_matches'), None)
        if fp_matches_req == 'true':
            fp_matches_req = True
            fp_search_or_matches_req = True
            from_timestamp = None
            until_timestamp = None
        else:
            fp_matches_req = False
    # @modified 20170916 - Feature #1996: Ionosphere - matches page
    # Handle both fp_search and fp_matches
    # if fp_search_req and request_args_len > 1:
    if fp_search_or_matches_req and request_args_len > 1:
        REQUEST_ARGS = ['fp_search',
                        'metric',
                        'metric_like',
                        'from_timestamp',
                        'until_timestamp',
                        'generation_greater_than',
                        # @added 20170315 - Feature #1960: ionosphere_layers
                        'layers_id_greater_than',
                        # @added 20170402 - Feature #2000: Ionosphere - validated
                        'validated_equals',
                        # @added 20170518 - Feature #1996: Ionosphere - matches page - matched_greater_than
                        'matched_greater_than',
                        'full_duration',
                        'enabled',
                        'tsfresh_version',
                        'generation',
                        'count_by_metric',
                        'count_by_matched',
                        'count_by_generation',
                        'count_by_checked',
                        'limit',
                        'order',
                        # @added 20170916 - Feature #1996: Ionosphere - matches page
                        'fp_matches',
                        # @added 20170917 - Feature #1996: Ionosphere - matches page
                        'fp_id', 'layer_id',
                        # @added 20180804 - Feature #2488: Allow user to specifically set metric as a derivative metric in training_data
                        'load_derivative_graphs',
                        # @added 20180917 - Feature #2602: Graphs in search_features_profiles
                        'show_graphs',
                        ]

        count_by_metric = None
        ordered_by = None
        limited_by = None
        get_metric_profiles = False
        not_metric_wildcard = True
        for i in request.args:
            key = str(i)
            if key not in REQUEST_ARGS:
                # @modified 20190524 - Branch #3002: docker
                # Return data
                # logger.error('error :: invalid request argument - %s' % (key))
                # return 'Bad Request', 400
                error_string = 'error :: invalid request argument - %s' % (key)
                logger.error(error_string)
                resp = json.dumps(
                    {'400 Bad Request': error_string})
                return flask_escape(resp), 400

            value = request.args.get(key, None)
            logger.info('request argument - %s=%s' % (key, str(value)))

            if key == 'order':
                order = str(value)
                if order == 'DESC':
                    ordered_by = 'DESC'
                if order == 'ASC':
                    ordered_by = 'ASC'
            if key == 'limit':
                limit = str(value)
                try:
                    test_limit = int(limit) + 0
                    limited_by = test_limit
                except:
                    logger.error('error :: limit is not an integer - %s' % str(limit))
                    limited_by = '30'

            if key == 'from_timestamp' or key == 'until_timestamp':
                timestamp_format_invalid = True
                if value == 'all':
                    timestamp_format_invalid = False
                # unix timestamp
                if value.isdigit():
                    timestamp_format_invalid = False
                # %Y%m%d %H:%M timestamp
                if timestamp_format_invalid:
                    value_strip_colon = value.replace(':', '')
                    new_value = value_strip_colon.replace(' ', '')
                    if new_value.isdigit():
                        timestamp_format_invalid = False
                if timestamp_format_invalid:
                    error_string = 'error :: invalid %s value passed %s' % (key, value)
                    logger.error('error :: invalid %s value passed %s' % (key, value))
                    # @modified 20190524 - Branch #3002: docker
                    # Return data
                    # return 'Bad Request', 400
                    error_string = 'error :: invalid request argument - %s' % (key)
                    logger.error(error_string)
                    resp = json.dumps(
                        {'400 Bad Request': error_string})
                    return flask_escape(resp), 400

                # @added 20190524 - Bug #3050: Ionosphere - Skyline and Graphite feedback
                #                   Branch #3002: docker
                # Added the missing definition of these 2 variables in the
                # fp_matches context
                if key == 'from_timestamp':
                    from_timestamp = value
                if key == 'until_timestamp':
                    until_timestamp = value

            if key == 'count_by_metric':
                count_by_metric = request.args.get(str('count_by_metric'), None)
                if count_by_metric == 'true':
                    count_by_metric = True
                else:
                    count_by_metric = False

            if key == 'metric':
                if str(value) == 'all' or str(value) == '*':
                    not_metric_wildcard = False
                    get_metric_profiles = True
                    metric = str(value)

            if key == 'metric' and not_metric_wildcard:
                try:
                    unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                except:
                    logger.error('error :: Webapp could not get the unique_metrics list from Redis')
                    logger.info(traceback.format_exc())
                    return 'Internal Server Error', 500
                metric_name = settings.FULL_NAMESPACE + str(value)

                # @added 20180423 - Feature #2034: analyse_derivatives
                #                   Branch #2270: luminosity
                metric_found_in_other_redis = False
                other_unique_metrics = []
                if metric_name not in unique_metrics and settings.OTHER_SKYLINE_REDIS_INSTANCES:
                    # @modified 20180519 - Feature #2378: Add redis auth to Skyline and rebrow
                    # for redis_ip, redis_port in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                    for redis_ip, redis_port, redis_password in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                        if not metric_found_in_other_redis:
                            try:
                                if redis_password:
                                    other_redis_conn = redis.StrictRedis(host=str(redis_ip), port=int(redis_port), password=str(redis_password))
                                else:
                                    other_redis_conn = redis.StrictRedis(host=str(redis_ip), port=int(redis_port))
                                other_unique_metrics = list(other_redis_conn.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                            except:
                                logger.error(traceback.format_exc())
                                logger.error('error :: failed to connect to Redis at %s on port %s' % (str(redis_ip), str(redis_port)))
                            if metric_name in other_unique_metrics:
                                metric_found_in_other_redis = True
                                logger.info('%s found in derivative_metrics in Redis at %s on port %s' % (metric_name, str(redis_ip), str(redis_port)))

                if metric_name not in unique_metrics and not metric_found_in_other_redis:
                    error_string = 'error :: no metric - %s - exists in Redis' % metric_name
                    logger.error(error_string)
                    resp = json.dumps(
                        {'results': error_string})
                    # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                    #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                    # return resp, 404
                    return flask_escape(resp), 404
                else:
                    get_metric_profiles = True
                    metric = str(value)

            # @added 20170917 - Feature #1996: Ionosphere - matches page
            matching = False
            metric_like = False
            if key == 'metric_like':
                if value == 'all':
                    metric_namespace_pattern = value.replace('all', '')

                metric_namespace_pattern = value.replace('%', '')
                if metric_namespace_pattern != '' and value != 'all':
                    try:
                        unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                    except:
                        trace = traceback.format_exc()
                        fail_msg = 'error :: Webapp could not get the unique_metrics list from Redis'
                        logger.error(fail_msg)
                        logger.info(traceback.format_exc())
                        return internal_error(fail_msg, trace)

                    matching = [s for s in unique_metrics if metric_namespace_pattern in s]
                    if len(matching) == 0:
                        error_string = 'error :: no metric like - %s - exists in Redis' % metric_namespace_pattern
                        logger.error(error_string)
                        resp = json.dumps(
                            {'results': error_string})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404
                if matching:
                    metric_like = str(value)

    if fp_search_req and request_args_len > 1:
        if count_by_metric:
            # @modified 20180717 - Task #2446: Optimize Ionosphere
            # Added missing search_success variable
            features_profiles, fps_count, mc, cc, gc, full_duration_list, enabled_list, tsfresh_version_list, generation_list, search_success, fail_msg, trace = ionosphere_search(False, True)

            return render_template(
                'ionosphere.html', fp_search=fp_search_req,
                fp_search_results=fp_search_req,
                features_profiles_count=fps_count, order=ordered_by,
                limit=limited_by, matched_count=mc, checked_count=cc,
                generation_count=gc, matched_from_datetime=matched_from_datetime,
                version=skyline_version,
                duration=(time.time() - start), print_debug=False), 200

        # @added 20180917 - Feature #2602: Graphs in search_features_profiles
        features_profiles_with_images = []
        show_graphs = False

        if get_metric_profiles:
            search_success = False
            try:
                fps, fps_count, mc, cc, gc, full_duration_list, enabled_list, tsfresh_version_list, generation_list, search_success, fail_msg, trace = ionosphere_search(False, True)
            except:
                trace = traceback.format_exc()
                fail_msg = 'error :: Webapp error with search_ionosphere'
                logger.error(fail_msg)
                return internal_error(fail_msg, trace)
            if not search_success:
                return internal_error(fail_msg, trace)

            # @added 20180917 - Feature #2602: Graphs in search_features_profiles
            if search_success and fps:
                show_graphs = request.args.get(str('show_graphs'), False)
                if show_graphs == 'true':
                    show_graphs = True
            if search_success and fps and show_graphs:
                # @modified 20190503 - Branch #2646: slack - linting
                # query_context = 'features_profiles'

                for fp_elements in fps:
                    # Get images
                    try:
                        fp_id = fp_elements[0]
                        base_name = fp_elements[2]
                        requested_timestamp = fp_elements[4]

                        # @modified 20181205 - Bug #2746: webapp time out - Graphs in search_features_profiles
                        #                      Feature #2602: Graphs in search_features_profiles
                        # This function was causing the webapp to time out due
                        # to fetching all the matched Graphite graphs
                        # mpaths, images, hdate, m_vars, ts_json, data_to_process, p_id, gimages, gmimages, times_matched, glm_images, l_id_matched, ts_fd, i_ts_json, anomalous_timeseries, f_id_matched, fp_details_list = ionosphere_metric_data(requested_timestamp, base_name, query_context, fp_id)
                        images, gimages = ionosphere_show_graphs(requested_timestamp, base_name, fp_id)

                        new_fp = []
                        for fp_element in fp_elements:
                            new_fp.append(fp_element)

                        # @added 20180918 - Feature #2602: Graphs in search_features_profiles
                        # The images are required to be sorted here in terms of
                        # only passing the Redis image (if present) and the
                        # full duration graph, as it is a bit too much
                        # achieve in the Jinja template.
                        full_duration_float = fp_elements[3]
                        full_duration = int(full_duration_float)
                        full_duration_in_hours = full_duration / 60 / 60
                        full_duration_in_hours_image_string = '.%sh.png' % str(int(full_duration_in_hours))

                        # @modified 20190503 - Branch #2646: slack - linting
                        # show_graph_images = []

                        redis_image = 'No Redis data graph'
                        full_duration_image = 'No full duration graph'
                        # @modified 20180918 - Feature #2602: Graphs in search_features_profiles
                        # Append individual redis_image and full_duration_image
                        # list elements instead of just added the images or
                        #  gimages list
                        # if images:
                        #     new_fp.append(images)
                        # else:
                        #     new_fp.append(gimages)
                        if images:
                            for image in images:
                                if '.redis.plot' in image:
                                    redis_image = image
                                if full_duration_in_hours_image_string in image:
                                    full_duration_image = image
                        else:
                            for image in gimages:
                                if '.redis.plot' in image:
                                    redis_image = image
                                if full_duration_in_hours_image_string in image:
                                    full_duration_image = image
                        if full_duration_image == 'No full duration graph':
                            for image in gimages:
                                if full_duration_in_hours_image_string in image:
                                    full_duration_image = image
                        new_fp.append(full_duration_image)
                        new_fp.append(redis_image)

                        features_profiles_with_images.append(new_fp)
                    except:
                        message = 'Uh oh ... a Skyline 500 :('
                        trace = traceback.format_exc()
                        return internal_error(message, trace)

            if not features_profiles_with_images:
                if fps:
                    for fp_elements in fps:
                        try:
                            new_fp = []
                            for fp_element in fp_elements:
                                new_fp.append(fp_element)
                            new_fp.append(None)
                            features_profiles_with_images.append(new_fp)
                        except:
                            message = 'Uh oh ... a Skyline 500 :('
                            trace = traceback.format_exc()
                            return internal_error(message, trace)

            # @modified 20170912 - Feature #2056: ionosphere - disabled_features_profiles
            # Added enabled_list to display DISABLED in search_features_profiles
            # page results.
            return render_template(
                'ionosphere.html', fp_search=fp_search_req,
                fp_search_results=fp_search_req, features_profiles=fps,
                for_metric=metric, order=ordered_by, limit=limited_by,
                matched_count=mc, checked_count=cc, generation_count=gc,
                enabled_list=enabled_list,
                matched_from_datetime=matched_from_datetime,
                # @added 20180917 - Feature #2602: Graphs in search_features_profiles
                features_profiles_with_images=features_profiles_with_images,
                show_graphs=show_graphs,
                version=skyline_version, duration=(time.time() - start),
                print_debug=False), 200

    # @added 20170916 - Feature #1996: Ionosphere - matches page
    if fp_matches_req:
        # @added 20170917 - Feature #1996: Ionosphere - matches page
        # Added by fp_id or layer_id as well
        fp_id = None
        layer_id = None
        # @added 20190619 - Feature #3084: Ionosphere - validated matches
        validated_equals = None
        for i in request.args:
            key = str(i)
            value = request.args.get(key, None)
            if key == 'fp_id':
                logger.info('request key %s set to %s' % (key, str(value)))
                try:
                    # @modified 20190524 - Branch #3002: docker
                    # test_fp_id = int(value) + 0
                    # if test_fp_id > 0:
                    #     fp_id = str(test_fp_id)
                    test_fp_id = int(value) + 1
                    if test_fp_id > -1:
                        fp_id = str(value)
                    else:
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # Test that the fp_id is an int first
                        # fp_id = None
                        logger.error('error :: invalid request argument - fp_id is not an int')
                        # @modified 20190524 - Branch #3002: docker
                        # Return data
                        # return 'Bad Request', 400
                        error_string = 'error :: invalid request argument - fp_id is not an int'
                        logger.error(error_string)
                        resp = json.dumps(
                            {'400 Bad Request': error_string})
                        return flask_escape(resp), 200

                    logger.info('fp_id now set to %s' % (str(fp_id)))
                except:
                    error_string = 'error :: the fp_id argument was passed but not as an int - %s' % str(value)
                    logger.error(error_string)
                    resp = json.dumps(
                        {'400 Bad Request': error_string})
                    # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                    #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                    # return resp, 404
                    return flask_escape(resp), 404
            if key == 'layer_id':
                logger.info('request key %s set to %s' % (key, str(value)))
                try:
                    test_layer_id = int(value) + 0
                    if test_layer_id > 0:
                        layer_id = str(test_layer_id)
                    else:
                        layer_id = None
                    logger.info('layer_id now set to %s' % (str(layer_id)))
                except:
                    error_string = 'error :: the layer_id argument was passed but not as an int - %s' % str(value)
                    logger.error(error_string)
                    resp = json.dumps(
                        {'400 Bad Request': error_string})
                    # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                    #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                    # return resp, 404
                    return flask_escape(resp), 404

            # @added 20190619 - Feature #3084: Ionosphere - validated matches
            if key == 'validated_equals':
                validated_equals = str(value)

        logger.info('get_fp_matches with arguments :: %s, %s, %s, %s, %s, %s, %s, %s' % (
            str(metric), str(metric_like), str(fp_id), str(layer_id),
            str(from_timestamp), str(until_timestamp), str(limited_by),
            str(ordered_by)))

        matches, fail_msg, trace = get_fp_matches(metric, metric_like, fp_id, layer_id, from_timestamp, until_timestamp, limited_by, ordered_by)
        if not matches:
            return internal_error(fail_msg, trace)

        # @added 20190619 - Feature #3084: Ionosphere - validated matches
        if validated_equals:
            filter_matches = True
            if validated_equals == 'any':
                filter_matches = False
            if validated_equals == 'true':
                filter_match_validation = 1
            if validated_equals == 'false':
                filter_match_validation = 0
            if validated_equals == 'invalid':
                filter_match_validation = 2
            if filter_matches:
                logger.info('matches filtered by validated = %s' % (
                    str(filter_match_validation)))

        return render_template(
            'ionosphere.html', fp_matches=fp_matches_req, for_metric=metric,
            fp_matches_results=matches, order=ordered_by, limit=limited_by,
            matched_from_datetime=matched_from_datetime,
            version=skyline_version, duration=(time.time() - start),
            print_debug=False), 200

    # @modified 20170118 - Feature #1862: Ionosphere features profiles search page
    # Added fp_search parameter
    # @modified 20170122 - Feature #1872: Ionosphere - features profile page by id only
    # Added fp_id parameter
    # @modified 20170305 - Feature #1960: ionosphere_layers
    # Added layers arguments d_condition to fp_layer
    # @modified 20160315 - Feature #1972: ionosphere_layers - use D layer boundary for upper limit
    # Added d_boundary_times
    # @modified 20170327 - Feature #2004: Ionosphere layers - edit_layers
    # Added layers_id and edit_fp_layers
    # @added 20170402 - Feature #2000: Ionosphere - validated
    IONOSPHERE_REQUEST_ARGS = [
        'timestamp', 'metric', 'metric_td', 'a_dated_list', 'timestamp_td',
        'requested_timestamp', 'fp_view', 'calc_features', 'add_fp',
        'features_profiles', 'fp_search', 'learn', 'fp_id', 'd_condition',
        'd_boundary_limit', 'd_boundary_times', 'e_condition', 'e_boundary_limit',
        'e_boundary_times', 'es_layer', 'es_day', 'f1_layer', 'f1_from_time',
        'f1_layer', 'f2_until_time', 'fp_layer', 'fp_layer_label',
        'add_fp_layer', 'layers_id', 'edit_fp_layers', 'validate_fp',
        'validated_equals',
        # @added 20170616 - Feature #2048: D1 ionosphere layer
        'd1_condition', 'd1_boundary_limit', 'd1_boundary_times',
        # @added 20170617 - Feature #2054: ionosphere.save.training_data
        'save_training_data', 'saved_td_label', 'saved_training_data',
        # added 20170908 - Feature #2056: ionosphere - disabled_features_profiles
        'disable_fp',
        # @added 20170917 - Feature #1996: Ionosphere - matches page
        'matched_fp_id', 'matched_layer_id',
        # @added 20180804 - Feature #2488: Allow user to specifically set metric as a derivative metric in training_data
        'load_derivative_graphs',
        # @added 20190601 - Feature #3084: Ionosphere - validated matches
        'match_validation',
        # @added 20190922 - Feature #2516: Add label to features profile
        'label',
    ]

    # @modified 20190503 - Branch #2646: slack - linting
    # determine_metric = False

    dated_list = False
    td_requested_timestamp = False

    # @modified 20190503 - Branch #2646: slack - linting
    # feature_profile_view = False

    calculate_features = False
    create_feature_profile = False
    fp_view = False
    fp_profiles = []
    # @added 20170118 - Feature #1862: Ionosphere features profiles search page
    fp_search = False
    # @added 20170120 -  Feature #1854: Ionosphere learn - generations
    # Added fp_learn and fp_fd_days parameters to allow the user to not learn at
    # use_full_duration_days
    fp_learn = False
    fp_fd_days = settings.IONOSPHERE_LEARN_DEFAULT_FULL_DURATION_DAYS

    # @added 20170327 - Feature #2004: Ionosphere layers - edit_layers
    #                   Task #2002: Review and correct incorrectly defined layers
    # Added the argument edit_fp_layers
    edit_fp_layers = False
    layers_id = None
    # @added 20170617 - Feature #2054: ionosphere.save.training_data
    save_training_data = False
    saved_training_data = False
    saved_td_label = False

    # added 20170908 - Feature #2056: ionosphere - disabled_features_profiles
    disable_fp = False

    # @added 20170917 - Feature #1996: Ionosphere - matches page
    matched_fp_id = False
    matched_layer_id = False
    # @added 20190601 - Feature #3084: Ionosphere - validated matches
    match_validated = 0

    # @added 20190922 - Feature #2516: Add label to features profile
    fp_label = None

    try:
        if request_args_present:
            timestamp_arg = False
            metric_arg = False
            metric_td_arg = False
            timestamp_td_arg = False
            # @added 20180804 - Feature #2488: Allow user to specifically set metric as a derivative metric in training_data
            set_derivative_metric = False

            if 'fp_view' in request.args:
                fp_view = request.args.get(str('fp_view'), None)
                base_name = request.args.get(str('metric'), None)

                # @added 20170917 - Feature #1996: Ionosphere - matches page
                if 'matched_fp_id' in request.args:
                    matched_fp_id = request.args.get(str('matched_fp_id'), None)
                if 'matched_layer_id' in request.args:
                    matched_layer_id = request.args.get(str('matched_layer_id'), None)
                # @added 20190601 - Feature #3084: Ionosphere - validated matches
                if 'match_validation' in request.args:
                    match_validated_str = request.args.get(str('match_validation'), None)
                    if match_validated_str:
                        try:
                            match_validated = int(match_validated_str)
                        except:
                            error_string = 'error :: invalid request argument - match_validation is not an int - %s' % str(match_validated_str)
                            logger.error(error_string)
                            resp = json.dumps(
                                {'results': error_string})
                            return flask_escape(resp), 400

                # @added 20170122 - Feature #1872: Ionosphere - features profile page by id only
                # Determine the features profile dir path for a fp_id
                if 'fp_id' in request.args:
                    # @added 20190116 - Cross-Site Scripting Security Vulnerability #85
                    #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                    # Test that the fp_id is an int first
                    try:
                        test_fp_id = request.args.get(str('fp_id'))
                        test_fp_id_valid = int(test_fp_id) + 1
                        logger.info('test_fp_id_valid tests OK with %s' % str(test_fp_id_valid))
                    except:
                        logger.error('error :: invalid request argument - fp_id is not an int')
                        # @modified 20190524 - Branch #3002: docker
                        # Return data
                        # return 'Bad Request', 400
                        error_string = 'error :: the fp_id argument was passed but not as an int - %s' % str(value)
                        logger.error(error_string)
                        resp = json.dumps(
                            {'results': error_string})
                        return flask_escape(resp), 400

                    fp_id = request.args.get(str('fp_id'), None)

                    # @modified 20190503 - Branch #2646: slack - linting
                    # metric_timestamp = 0

                    try:
                        fp_details, fp_details_successful, fail_msg, traceback_format_exc, fp_details_object = features_profile_details(fp_id)
                        anomaly_timestamp = int(fp_details_object['anomaly_timestamp'])
                        created_timestamp = fp_details_object['created_timestamp']
                    except:
                        trace = traceback.format_exc()
                        message = 'failed to get features profile details for id %s' % str(fp_id)
                        return internal_error(message, trace)
                    if not fp_details_successful:
                        trace = traceback.format_exc()
                        fail_msg = 'error :: features_profile_details failed'
                        return internal_error(fail_msg, trace)

                    use_timestamp = 0
                    metric_timeseries_dir = base_name.replace('.', '/')
                    # @modified 20170126 - Feature #1872: Ionosphere - features profile page by id only
                    # The the incorrect logic, first it should be checked if
                    # there is a use_full_duration parent timestamp
                    dt = str(created_timestamp)
                    naive = datetime.datetime.strptime(dt, '%Y-%m-%d %H:%M:%S')
                    pytz_tz = settings.SERVER_PYTZ_TIMEZONE
                    local = pytz.timezone(pytz_tz)
                    local_dt = local.localize(naive, is_dst=None)
                    utc_dt = local_dt.astimezone(pytz.utc)
                    unix_created_timestamp = utc_dt.strftime('%s')
                    features_profiles_data_dir = '%s/%s/%s' % (
                        settings.IONOSPHERE_PROFILES_FOLDER, metric_timeseries_dir,
                        str(unix_created_timestamp))
                    if os.path.exists(features_profiles_data_dir):
                        use_timestamp = int(unix_created_timestamp)
                    else:
                        logger.error('no timestamp feature profiles data dir found for feature profile id %s at %s' % (str(fp_id), str(features_profiles_data_dir)))

                    # @added 20170915 - Bug #2162: ionosphere - mismatching timestamp metadata
                    #                   Feature #1872: Ionosphere - features profile page by id only
                    # Iterate back a few seconds as the features profile dir and
                    # file resources may have a slight offset timestamp from the
                    # created_timestamp which is based on MySQL CURRENT_TIMESTAMP
                    if use_timestamp == 0:
                        check_back_to_timestamp = int(unix_created_timestamp) - 10
                        check_timestamp = int(unix_created_timestamp) - 1
                        while check_timestamp > check_back_to_timestamp:
                            features_profiles_data_dir = '%s/%s/%s' % (
                                settings.IONOSPHERE_PROFILES_FOLDER, metric_timeseries_dir,
                                str(check_timestamp))
                            if os.path.exists(features_profiles_data_dir):
                                use_timestamp = int(check_timestamp)
                                check_timestamp = check_back_to_timestamp - 1
                            else:
                                check_timestamp -= 1

                    features_profiles_data_dir = '%s/%s/%s' % (
                        settings.IONOSPHERE_PROFILES_FOLDER, metric_timeseries_dir,
                        str(anomaly_timestamp))
                    # @modified 20170126 - Feature #1872: Ionosphere - features profile page by id only
                    # This was the incorrect logic, first it should be checked if
                    # there is a use_full_duration parent timestamp
                    if use_timestamp == 0:
                        if os.path.exists(features_profiles_data_dir):
                            use_timestamp = int(anomaly_timestamp)
                        else:
                            logger.error('no timestamp feature profiles data dir found for feature profile id %s at %s' % (str(fp_id), str(features_profiles_data_dir)))

                    if use_timestamp == 0:
                        logger.error('no timestamp feature profiles data dir found for feature profile id - %s' % str(fp_id))

                        # @added 20180420 - Branch #2270: luminosity
                        # Use settings.ALTERNATIVE_SKYLINE_URLS if they are
                        # declared
                        try:
                            use_alternative_urls = settings.ALTERNATIVE_SKYLINE_URLS
                        except:
                            use_alternative_urls = False

                        if use_alternative_urls:
                            alternative_urls = []
                            for alt_url in use_alternative_urls:
                                alt_redirect_url = '%s/ionosphere?fp_view=true&fp_id=%s&metric=%s' % (str(alt_url), str(fp_id), str(base_name))
                                alternative_urls.append(alt_redirect_url)
                            message = 'no timestamp feature profiles data dir found on this Skyline instance try at the alternative URLS listed below:'
                            logger.info('passing alternative_urls - %s' % str(alternative_urls))
                            try:
                                return render_template(
                                    'ionosphere.html', display_message=message,
                                    alternative_urls=alternative_urls,
                                    fp_view=True,
                                    version=skyline_version, duration=(time.time() - start),
                                    print_debug=True), 200
                            except:
                                message = 'Uh oh ... a Skyline 500 :('
                                trace = traceback.format_exc()
                                return internal_error(message, trace)

                        resp = json.dumps(
                            {'results': 'Error: no timestamp feature profiles data dir found for feature profile id - ' + str(fp_id) + ' - go on... nothing here.'})
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 400
                        return flask_escape(resp), 400

                    redirect_url = '%s/ionosphere?fp_view=true&timestamp=%s&metric=%s' % (settings.SKYLINE_URL, str(use_timestamp), base_name)

                    # @added 20180815 - Feature #2430: Ionosphere validate learnt features profiles page
                    validate_fp_req = False
                    if 'validate_fp' in request.args:
                        validate_fp_req = request.args.get(str('validate_fp'), None)
                        if validate_fp_req == 'true':
                            validate_fp_req = True
                    if validate_fp_req:
                        redirect_url = '%s/ionosphere?fp_view=true&timestamp=%s&metric=%s&validate_fp=true' % (
                            settings.SKYLINE_URL, str(use_timestamp), base_name)

                    # @added 20180816 - Feature #2430: Ionosphere validate learnt features profiles page
                    disable_fp_req = False
                    if 'disable_fp' in request.args:
                        disable_fp_id = request.args.get(str('disable_fp'), None)
                        if isinstance(int(disable_fp_id), int):
                            disable_fp_req = True
                    if disable_fp_req:
                        redirect_url = '%s/ionosphere?fp_view=true&timestamp=%s&metric=%s&disable_fp=%s' % (
                            settings.SKYLINE_URL, str(use_timestamp), base_name,
                            str(disable_fp_id))

                    # @added 20170917 - Feature #1996: Ionosphere - matches page
                    if matched_fp_id:
                        if matched_fp_id != 'False':
                            redirect_url = '%s/ionosphere?fp_view=true&timestamp=%s&metric=%s&matched_fp_id=%s' % (
                                settings.SKYLINE_URL, str(use_timestamp), base_name,
                                str(matched_fp_id))
                    if matched_layer_id:
                        if matched_layer_id != 'False':
                            redirect_url = '%s/ionosphere?fp_view=true&timestamp=%s&metric=%s&matched_layer_id=%s' % (
                                settings.SKYLINE_URL, str(use_timestamp), base_name,
                                str(matched_layer_id))

                    # @added 20190601 - Feature #3084: Ionosphere - validated matches
                    if matched_fp_id or matched_layer_id:
                        if 'match_validation' in request.args:
                            if match_validated > 0:
                                validate_matched_redirect_url = '%s&match_validation=%s' % (
                                    redirect_url, str(match_validated))
                                redirect_url = validate_matched_redirect_url

                    # @modified 20170327 - Feature #2004: Ionosphere layers - edit_layers
                    #                      Task #2002: Review and correct incorrectly defined layers
                    # Build the query string from the previous parameters
                    if 'edit_fp_layers' in request.args:
                        redirect_url = '%s/ionosphere?timestamp=%s' % (settings.SKYLINE_URL, str(use_timestamp))
                        for i in request.args:
                            key = str(i)
                            if key == 'timestamp':
                                continue
                            value = request.args.get(key, None)
                            new_redirect_url = '%s&%s=%s' % (
                                redirect_url, str(key), str(value))
                            redirect_url = new_redirect_url
                    if 'edit_fp_layers' in request.args:
                        logger.info('not returning redirect as edit_fp_layers request')
                    else:
                        logger.info('returned redirect on original request - %s' % str(redirect_url))
                        return redirect(redirect_url, code=302)

            for i in request.args:
                key = str(i)
                if key not in IONOSPHERE_REQUEST_ARGS:
                    logger.error('error :: invalid request argument - %s' % (key))
                    # @modified 20190524 - Branch #3002: docker
                    # Return data
                    # return 'Bad Request', 400
                    error_string = 'error :: invalid request argument - %s' % (key)
                    logger.error(error_string)
                    resp = json.dumps(
                        {'400 Bad Request': error_string})
                    return flask_escape(resp), 400

                value = request.args.get(key, None)
                logger.info('request argument - %s=%s' % (key, str(value)))

                if key == 'calc_features':
                    if str(value) == 'true':
                        calculate_features = True

                if key == 'add_fp':
                    if str(value) == 'true':
                        create_feature_profile = True

                # @added 20170317 - Feature #1960: ionosphere_layers - allow for floats
                if key == 'd_boundary_limit':
                    try:
                        d_boundary_limit = float(value)
                    except ValueError:
                        logger.error('error :: invalid request argument - %s is not numeric - %s' % (key, str(value)))
                        return 'Bad Request\n\ninvalid request argument - %s is not numeric - %s' % (key, str(value)), 400
                    logger.info('request argument OK - %s=%s' % (key, str(d_boundary_limit)))
                if key == 'e_boundary_limit':
                    try:
                        e_boundary_limit = float(value)
                    except ValueError:
                        logger.error('error :: invalid request argument - %s is not numeric - %s' % (key, str(value)))
                        return 'Bad Request\n\ninvalid request argument - %s is not numeric - %s' % (key, str(value)), 400
                    logger.info('request argument OK - %s=%s' % (key, str(e_boundary_limit)))

                if key == 'fp_view':
                    if str(value) == 'true':
                        fp_view = True

                # @added 20170118 - Feature #1862: Ionosphere features profiles search page
                # Added fp_search parameter
                if key == 'fp_search':
                    if str(value) == 'true':
                        fp_search = True

                # @added 20170120 -  Feature #1854: Ionosphere learn - generations
                # Added fp_learn parameter
                if key == 'learn':
                    if str(value) == 'true':
                        fp_learn = True
                    # @added 20170305 - Feature #1960: ionosphere_layers
                    # Being passed through as a boolean from the Create features
                    # profile arguments and I cannot be arsed to track it down
                    if str(value) == 'True':
                        fp_learn = True

                if key == 'features_profiles':
                    fp_profiles = str(value)
                    # @added 20190503 - Branch #2646: slack - linting
                    logger.info('fp_profiles is %s' % fp_profiles)

                # @added 20170327 - Feature #2004: Ionosphere layers - edit_layers
                #                   Task #2002: Review and correct incorrectly defined layers
                # Added layers and edit_fp_layers
                if key == 'layers_id':
                    test_layer_id = str(value)
                    try:
                        layers_id = int(test_layer_id)
                    except:
                        logger.info('bad request argument - %s=%s not numeric' % (str(key), str(value)))
                        resp = json.dumps(
                            {'results': 'Error: not a numeric id for the layers_id argument - %s' + str(value) + ' - please pass a proper id'})
                if key == 'edit_fp_layers':
                    if str(value) == 'true':
                        edit_fp_layers = True
                    if str(value) == 'True':
                        edit_fp_layers = True
                    if edit_fp_layers:
                        logger.info('edit_fp_layers is set to %s' % (str(edit_fp_layers)))

                if key == 'a_dated_list':
                    if str(value) == 'true':
                        dated_list = True

                if key == 'requested_timestamp':
                    valid_rt_timestamp = False
                    if str(value) == 'False':
                        valid_rt_timestamp = True
                    if not valid_rt_timestamp:
                        if not len(str(value)) == 10:
                            logger.info('bad request argument - %s=%s not an epoch timestamp' % (str(key), str(value)))
                            resp = json.dumps(
                                {'results': 'Error: not an epoch timestamp for ' + str(key) + ' - ' + str(value) + ' - please pass a proper epoch timestamp'})
                        else:
                            try:
                                timestamp_numeric = int(value) + 1
                                valid_rt_timestamp = True
                            except:
                                valid_timestamp = False
                                logger.info('bad request argument - %s=%s not numeric' % (str(key), str(value)))
                                resp = json.dumps(
                                    {'results': 'Error: not a numeric epoch timestamp for ' + str(key) + ' - ' + str(value) + ' - please pass a proper epoch timestamp'})

                    if not valid_rt_timestamp:
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 400
                        return flask_escape(resp), 400

                # @added 20170617 - Feature #2054: ionosphere.save.training_data
                if key == 'save_training_data':
                    if str(value) == 'true':
                        save_training_data = True
                if key == 'saved_td_label':
                    saved_td_label = str(value)
                if key == 'saved_training_data':
                    if str(value) == 'true':
                        saved_training_data = True
                check_for_purged = False
                if not saved_training_data:
                    if not fp_view:
                        check_for_purged = True

                if key == 'label':
                    label_arg = request.args.get('label')
                    label = label_arg[:255]
                    logger.info('label - %s ' % (str(value)))

                if key == 'timestamp' or key == 'timestamp_td':
                    valid_timestamp = True
                    if not len(str(value)) == 10:
                        valid_timestamp = False
                        logger.info('bad request argument - %s=%s not an epoch timestamp' % (str(key), str(value)))
                        resp = json.dumps(
                            {'results': 'Error: not an epoch timestamp for ' + str(key) + ' - ' + str(value) + ' - please pass a proper epoch timestamp'})
                    if valid_timestamp:
                        try:
                            timestamp_numeric = int(value) + 1
                            # @added 20190503 - Branch #2646: slack - linting
                            logger.info('timestamp_numeric tests OK with %s' % str(timestamp_numeric))
                        except:
                            valid_timestamp = False
                            logger.info('bad request argument - %s=%s not numeric' % (str(key), str(value)))
                            resp = json.dumps(
                                {'results': 'Error: not a numeric epoch timestamp for ' + str(key) + ' - ' + str(value) + ' - please pass a proper epoch timestamp'})

                    if not valid_timestamp:
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 400
                        return flask_escape(resp), 400

                    # if not fp_view:
                    if check_for_purged:
                        ionosphere_data_dir = '%s/%s' % (settings.IONOSPHERE_DATA_FOLDER, str(value))
                        if not isdir(ionosphere_data_dir):
                            valid_timestamp = False
                            now = time.time()
                            purged_timestamp = int(now) - int(settings.IONOSPHERE_KEEP_TRAINING_TIMESERIES_FOR)
                            if int(value) < purged_timestamp:
                                logger.info('%s=%s timestamp it to old to have training data' % (key, str(value)))
                                resp = json.dumps(
                                    {'results': 'Error: timestamp too old no training data exists, training data has been purged'})
                            else:
                                logger.error('%s=%s no timestamp training data dir found - %s' % (key, str(value), ionosphere_data_dir))
                                # @added 20180713 - Branch #2270: luminosity
                                # Use settings.ALTERNATIVE_SKYLINE_URLS if they are
                                # declared
                                try:
                                    use_alternative_urls = settings.ALTERNATIVE_SKYLINE_URLS
                                except:
                                    use_alternative_urls = False
                                if use_alternative_urls:
                                    base_name = request.args.get(str('metric'), None)
                                    alternative_urls = []
                                    for alt_url in use_alternative_urls:
                                        alt_redirect_url = '%s/ionosphere?timestamp=%s&metric=%s' % (str(alt_url), str(value), str(base_name))
                                        if len(use_alternative_urls) == 1:
                                            return redirect(alt_redirect_url)
                                        alternative_urls.append(alt_redirect_url)
                                    message = 'no training data dir exists on this Skyline instance try at the alternative URLS listed below:'
                                    logger.info('passing alternative_urls - %s' % str(alternative_urls))
                                    try:
                                        return render_template(
                                            'ionosphere.html', display_message=message,
                                            alternative_urls=alternative_urls,
                                            fp_view=True,
                                            version=skyline_version, duration=(time.time() - start),
                                            print_debug=True), 200
                                    except:
                                        message = 'Uh oh ... a Skyline 500 :('
                                        trace = traceback.format_exc()
                                        return internal_error(message, trace)
                                else:
                                    resp = json.dumps(
                                        {'results': 'Error: no training data dir exists - ' + ionosphere_data_dir + ' - go on... nothing here.'})

                    if not valid_timestamp:
                        # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                        #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                        # return resp, 404
                        return flask_escape(resp), 404

                if key == 'timestamp':
                    requested_timestamp = str(value)
                    timestamp_arg = True

                if key == 'timestamp_td':
                    timestamp_td_arg = True
                    requested_timestamp_td = str(value)
                    # determine_metric = True

                if key == 'requested_timestamp':
                    td_requested_timestamp = str(value)

                if key == 'metric' or key == 'metric_td':
                    try:
                        unique_metrics = list(REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                    except:
                        logger.error('error :: Webapp could not get the unique_metrics list from Redis')
                        logger.info(traceback.format_exc())
                        return 'Internal Server Error', 500
                    metric_name = settings.FULL_NAMESPACE + str(value)

                    metric_found = False
                    if metric_name not in unique_metrics and settings.OTHER_SKYLINE_REDIS_INSTANCES:
                        # @modified 20180519 - Feature #2378: Add redis auth to Skyline and rebrow
                        # for redis_ip, redis_port in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                        for redis_ip, redis_port, redis_password in settings.OTHER_SKYLINE_REDIS_INSTANCES:
                            other_unique_metrics = []
                            if not metric_found:
                                try:
                                    if redis_password:
                                        OTHER_REDIS_CONN = redis.StrictRedis(host=str(redis_ip), port=int(redis_port), password=str(redis_password))
                                    else:
                                        OTHER_REDIS_CONN = redis.StrictRedis(host=str(redis_ip), port=int(redis_port))
                                    other_unique_metrics = list(OTHER_REDIS_CONN.smembers(settings.FULL_NAMESPACE + 'unique_metrics'))
                                    logger.info('metric found in Redis at %s on port %s' % (str(redis_ip), str(redis_port)))
                                except:
                                    logger.error(traceback.format_exc())
                                    logger.error('error :: failed to connect to Redis at %s on port %s' % (str(redis_ip), str(redis_port)))
                            if metric_name in other_unique_metrics:
                                metric_found = True

                    # if metric_name not in unique_metrics:
                    if metric_name not in unique_metrics and not metric_found:
                        # @added 20170917 - Bug #2158: webapp - redis metric check - existing but sparsely represented metrics
                        # If this is an fp_view=true, it means that either the
                        # metric is sparsely represented or no longer exists,
                        # but an fp exists so continue and do not 404
                        if fp_view:
                            logger.info('%s not in Redis, but fp passed so continuing' % metric_name)
                        else:
                            error_string = 'error :: no metric - %s - exists in Redis' % metric_name
                            logger.error(error_string)
                            resp = json.dumps(
                                {'results': error_string})
                            # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                            #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                            # return resp, 404
                            return flask_escape(resp), 404

                if key == 'metric':
                    metric_arg = True

                if key == 'metric_td':
                    metric_td_arg = True

                if metric_arg or metric_td_arg:
                    if key == 'metric' or key == 'metric_td':
                        base_name = str(value)

                # @added 20180804 - Feature #2488: Allow user to specifically set metric as a derivative metric in training_data
                if timestamp_arg and metric_arg:
                    if key == 'load_derivative_graphs':
                        if str(value) == 'true':
                            set_derivative_metric = set_metric_as_derivative(skyline_app, base_name)
                            # @added 20180918 - Feature #2488: Allow user to specifically set metric as a derivative metric in training_data
                            # Remove any graphite_now png files that are present
                            # so that the webapp recreates the pngs as
                            # nonNegativeDerivative graphs.
                            # TODO - handle caching
                            try:
                                timeseries_dir = base_name.replace('.', '/')
                                ionosphere_data_dir = '%s/%s/%s' % (
                                    settings.IONOSPHERE_DATA_FOLDER,
                                    requested_timestamp, timeseries_dir)
                                pattern = 'graphite_now'
                                for f in os.listdir(ionosphere_data_dir):
                                    if re.search(pattern, f):
                                        remove_graphite_now_file = os.path.join(ionosphere_data_dir, f)
                                        os.remove(remove_graphite_now_file)
                                        logger.info('removed graphite_now image at user request - %s' % remove_graphite_now_file)
                            except:
                                logger.error('failed to remove graphite_now images')
                if set_derivative_metric:
                    return_url = '%s/ionosphere?timestamp=%s&metric=%s' % (str(settings.SKYLINE_URL), str(requested_timestamp), str(base_name))
                    return redirect(return_url)

                if timestamp_arg and metric_arg:
                    timeseries_dir = base_name.replace('.', '/')

                    if not fp_view:
                        ionosphere_data_dir = '%s/%s/%s' % (
                            settings.IONOSPHERE_DATA_FOLDER,
                            requested_timestamp, timeseries_dir)

                        # @added 20170617 - Feature #2054: ionosphere.save.training_data
                        if saved_training_data:
                            ionosphere_data_dir = '%s_saved/%s/%s' % (
                                settings.IONOSPHERE_DATA_FOLDER,
                                requested_timestamp, timeseries_dir)

                        if not isdir(ionosphere_data_dir):
                            logger.info(
                                '%s=%s no timestamp metric training data dir found - %s' %
                                (key, str(value), ionosphere_data_dir))
                            resp = json.dumps(
                                {'results': 'Error: no training data dir exists - ' + ionosphere_data_dir + ' - go on... nothing here.'})
                            # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                            #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                            # return resp, 404
                            return flask_escape(resp), 404
                    else:
                        ionosphere_profiles_dir = '%s/%s/%s' % (
                            settings.IONOSPHERE_PROFILES_FOLDER, timeseries_dir,
                            requested_timestamp, )

                        if not isdir(ionosphere_profiles_dir):
                            logger.info(
                                '%s=%s no timestamp metric features profile dir found - %s' %
                                (key, str(value), ionosphere_profiles_dir))

                            # @added 20180715 - Branch #2270: luminosity
                            # Use settings.ALTERNATIVE_SKYLINE_URLS if they are
                            # declared and redirect to alternative URL/s if no
                            # features profile directory exists on the Skyline
                            # instance.
                            try:
                                use_alternative_urls = settings.ALTERNATIVE_SKYLINE_URLS
                            except:
                                use_alternative_urls = False
                            if use_alternative_urls:
                                base_name = request.args.get(str('metric'), None)
                                alternative_urls = []
                                for alt_url in use_alternative_urls:
                                    alt_redirect_url_base = '%s/ionosphere' % str(alt_url)
                                    request_url = str(request.url)
                                    request_endpoint = '%s/ionosphere' % str(settings.SKYLINE_URL)
                                    alt_redirect_url = request_url.replace(request_endpoint, alt_redirect_url_base, 1)
                                    if len(use_alternative_urls) == 1:
                                        logger.info('redirecting to %s' % str(alt_redirect_url))
                                        return redirect(alt_redirect_url)
                                    alternative_urls.append(alt_redirect_url)
                                message = 'no features profile dir exists on this Skyline instance try at the alternative URLS listed below:'
                                logger.info('passing alternative_urls - %s' % str(alternative_urls))
                                try:
                                    return render_template(
                                        'ionosphere.html', display_message=message,
                                        alternative_urls=alternative_urls,
                                        fp_view=True,
                                        version=skyline_version, duration=(time.time() - start),
                                        print_debug=True), 200
                                except:
                                    message = 'Uh oh ... a Skyline 500 :('
                                    trace = traceback.format_exc()
                                    return internal_error(message, trace)
                            else:
                                resp = json.dumps(
                                    {'results': 'Error: no features profile dir exists - ' + ionosphere_profiles_dir + ' - go on... nothing here.'})
                                # @modified 20190116 - Cross-Site Scripting Security Vulnerability #85
                                #                      Bug #2816: Cross-Site Scripting Security Vulnerability
                                # return resp, 404
                                return flask_escape(resp), 404

        logger.info('arguments validated - OK')

    except:
        message = 'Uh oh ... a Skyline 500 :('
        trace = traceback.format_exc()
        return internal_error(message, trace)

    debug_on = False

    if fp_view:
        context = 'features_profiles'
    else:
        context = 'training_data'

    # @added 20170617 - Feature #2054: ionosphere.save.training_data
    if saved_training_data:
        context = 'saved_training_data'

    fp_view_on = fp_view

    do_first = False
    if fp_view:
        do_first = True
        args = [
            'timestamp', 'metric', 'metric_td', 'timestamp_td',
            'requested_timestamp', 'features_profiles']
        for i_arg in args:
            if i_arg in request.args:
                do_first = False

    if request_args_len == 0 or dated_list:
        do_first = True

    if do_first:
        listed_by = 'metric'
        if dated_list:
            listed_by = 'date'
        try:
            mpaths, unique_m, unique_ts, hdates = ionosphere_data(False, 'all', context)
            return render_template(
                'ionosphere.html', unique_metrics=unique_m, list_by=listed_by,
                unique_timestamps=unique_ts, human_dates=hdates,
                metric_td_dirs=zip(unique_ts, hdates), td_files=mpaths,
                requested_timestamp=td_requested_timestamp, fp_view=fp_view_on,
                matched_from_datetime=matched_from_datetime,
                version=skyline_version, duration=(time.time() - start),
                print_debug=debug_on), 200
        except:
            message = 'Uh oh ... a Skyline 500 :('
            trace = traceback.format_exc()
            return internal_error(message, trace)

    # @added 20170118 - Feature #1862: Ionosphere features profiles search page
    # Added fp_search parameter
    fp_search_param = None
    if fp_search:
        logger.debug('debug :: fp_search was True')
        fp_search_param = True
        listed_by = 'search'
        # get_options = [
        #     'full_duration', 'enabled', 'tsfresh_version', 'generation']
        fd_list = None
        try:
            # @modified 20170221 - Feature #1862: Ionosphere features profiles search page
            # fd_list, en_list, tsfresh_list, gen_list, fail_msg, trace = ionosphere_search_defaults(get_options)
            features_profiles, fp_count, mc, cc, gc, fd_list, en_list, tsfresh_list, gen_list, fail_msg, trace = ionosphere_search(True, False)
            logger.debug('debug :: fd_list - %s' % str(fd_list))
        except:
            message = 'Uh oh ... a Skyline 500 :('
            trace = traceback.format_exc()
            return internal_error(message, trace)

        if fd_list:
            try:
                return render_template(
                    'ionosphere.html', list_by=listed_by, fp_search=fp_search_param,
                    full_duration_list=fd_list, enabled_list=en_list,
                    tsfresh_version_list=tsfresh_list, generation_list=gen_list,
                    matched_from_datetime=matched_from_datetime,
                    version=skyline_version, duration=(time.time() - start),
                    print_debug=debug_on), 200
            except:
                message = 'Uh oh ... a Skyline 500 :('
                trace = traceback.format_exc()
                return internal_error(message, trace)

    if metric_td_arg:
        listed_by = 'metric_td_dirs'
        try:
            mpaths, unique_m, unique_ts, hdates = ionosphere_data(False, base_name, context)
            return render_template(
                'ionosphere.html', metric_td_dirs=zip(unique_ts, hdates),
                list_by=listed_by, for_metric=base_name, td_files=mpaths,
                requested_timestamp=td_requested_timestamp, fp_view=fp_view_on,
                matched_from_datetime=matched_from_datetime,
                version=skyline_version, duration=(time.time() - start),
                print_debug=debug_on), 200
        except:
            message = 'Uh oh ... a Skyline 500 :('
            trace = traceback.format_exc()
            return internal_error(message, trace)

    if timestamp_td_arg:
        # Note to self.  Can we carry the referring timestamp through and when
        # a metric is selected the time is the only one wrapped in <code> .e.g red?
        listed_by = 'timestamp_td_dirs'
        try:
            mpaths, unique_m, unique_ts, hdates = ionosphere_get_metrics_dir(requested_timestamp_td, context)
            return render_template(
                'ionosphere.html', unique_metrics=unique_m, list_by=listed_by,
                unique_timestamps=unique_ts, human_dates=hdates, td_files=mpaths,
                metric_td_dirs=zip(unique_ts, hdates),
                requested_timestamp=td_requested_timestamp, fp_view=fp_view_on,
                matched_from_datetime=matched_from_datetime,
                version=skyline_version, duration=(time.time() - start),
                print_debug=debug_on), 200
        except:
            message = 'Uh oh ... a Skyline 500 :('
            trace = traceback.format_exc()
            return internal_error(message, trace)

    if timestamp_arg and metric_arg:
        try:
            # @modified 20170104 - Feature #1842: Ionosphere - Graphite now graphs
            # Added the full_duration_in_hours and changed graph color from blue to orange
            # full_duration_in_hours
            # GRAPH_URL = GRAPHITE_PROTOCOL + '://' + GRAPHITE_HOST + ':' + GRAPHITE_PORT + '/render/?width=1400&from=-' + TARGET_HOURS + 'hour&target='
            # A regex is required to change the TARGET_HOURS, no? extend do not modify?
            # Not certain will review after Dude morning excersion
            graph_url = '%scactiStyle(%s)%s&colorList=blue' % (
                settings.GRAPH_URL, base_name, settings.GRAPHITE_GRAPH_SETTINGS)
        except:
            graph_url = False

        # @added 20170604 - Feature #2034: analyse_derivatives
        # Added nonNegativeDerivative to strictly
        # increasing monotonically metrics in graph_url
        known_derivative_metric = False
        try:
            derivative_metrics = list(REDIS_CONN.smembers('derivative_metrics'))
        except:
            derivative_metrics = []
        redis_metric_name = '%s%s' % (settings.FULL_NAMESPACE, str(base_name))
        if redis_metric_name in derivative_metrics:
            known_derivative_metric = True
        if known_derivative_metric:
            try:
                non_derivative_metrics = list(REDIS_CONN.smembers('non_derivative_metrics'))
            except:
                non_derivative_metrics = []
            skip_derivative = in_list(redis_metric_name, non_derivative_metrics)
            if skip_derivative:
                known_derivative_metric = False
        if known_derivative_metric:
            try:
                graph_url = '%scactiStyle(nonNegativeDerivative(%s))%s&colorList=blue' % (
                    settings.GRAPH_URL, base_name, settings.GRAPHITE_GRAPH_SETTINGS)
            except:
                graph_url = False

        # @added 20170327 - Feature #2004: Ionosphere layers - edit_layers
        #                   Task #2002: Review and correct incorrectly defined layers
        layers_updated = False
        if 'edit_fp_layers' in request.args:
            edit_fp_layers_arg = request.args.get('edit_fp_layers', False)
            if edit_fp_layers_arg == 'true':
                edit_fp_layers = True
                logger.info('editing layers id - %s' % str(layers_id))
            else:
                logger.info('not editing layers id - %s' % str(layers_id))

        if edit_fp_layers:
            logger.info('editing layers id - %s' % str(layers_id))
            try:
                layers_updated, fail_msg, traceback_format_exc = edit_ionosphere_layers(layers_id)
                logger.info('updated layers id - %s' % str(layers_id))
            except:
                trace = traceback.format_exc()
                message = 'failed to update layer calling edit_ionosphere_layers'
                return internal_error(message, trace)
            if not layers_updated:
                trace = 'none'
                message = 'failed to update layer'
                return internal_error(message, trace)
        else:
            logger.info('not editing layers')

        features = None
        f_calc = 'none'
        fp_exists = False
        fp_id = None

        if calculate_features or create_feature_profile or fp_view:
            try:
                fp_csv, successful, fp_exists, fp_id, fail_msg, traceback_format_exc, f_calc = calculate_features_profile(skyline_app, requested_timestamp, base_name, context)
            except:
                trace = traceback.format_exc()
                message = 'failed to calculate features'
                return internal_error(message, trace)

            if not successful:
                return internal_error(fail_msg, traceback_format_exc)
            if os.path.isfile(str(fp_csv)):
                features = []
                with open(fp_csv, 'rb') as fr:
                    reader = csv.reader(fr, delimiter=',')
                    for i, line in enumerate(reader):
                        features.append([str(line[0]), str(line[1])])

        generation_zero = False
        if create_feature_profile or fp_view:
            if create_feature_profile:
                # Submit to Ionosphere to run tsfresh on
                create_feature_profile = True
            if not fp_id:
                # @modified 20170114 -  Feature #1854: Ionosphere learn - generations
                # Added parent_id and generation as all features profiles that
                # are created via the UI will be generation 0
                parent_id = 0
                generation = 0
                ionosphere_job = 'learn_fp_human'
                # @added 20190503 - Branch #2646: slack
                # Added slack_ionosphere_job
                slack_ionosphere_job = ionosphere_job
                try:
                    # @modified 20170120 -  Feature #1854: Ionosphere learn - generations
                    # Added fp_learn parameter to allow the user to not learn the
                    # use_full_duration_days
                    # fp_id, fp_in_successful, fp_exists, fail_msg, traceback_format_exc = create_features_profile(skyline_app, requested_timestamp, base_name, context, ionosphere_job, parent_id, generation, fp_learn)
                    # @modified 20190503 - Branch #2646: slack
                    # Added slack_ionosphere_job
                    # fp_id, fp_in_successful, fp_exists, fail_msg, traceback_format_exc = create_features_profile(skyline_app, requested_timestamp, base_name, context, ionosphere_job, parent_id, generation, fp_learn)
                    # @modified 20190919 - Feature #3230: users DB table
                    #                      Ideas #2476: Label and relate anomalies
                    #                      Feature #2516: Add label to features profile
                    # Added user_id and label
                    # fp_id, fp_in_successful, fp_exists, fail_msg, traceback_format_exc = create_features_profile(skyline_app, requested_timestamp, base_name, context, ionosphere_job, parent_id, generation, fp_learn, slack_ionosphere_job)
                    fp_id, fp_in_successful, fp_exists, fail_msg, traceback_format_exc = create_features_profile(skyline_app, requested_timestamp, base_name, context, ionosphere_job, parent_id, generation, fp_learn, slack_ionosphere_job, user_id, label)
                    if create_feature_profile:
                        generation_zero = True
                except:
                    # @modified 20161209 -  - Branch #922: ionosphere
                    #                        Task #1658: Patterning Skyline Ionosphere
                    # Use raise and traceback.format_exc() to carry
                    # ionosphere_backend.py through to the rendered page for the user, e.g
                    # me.
                    # trace = traceback_format_exc
                    trace = traceback.format_exc()
                    message = 'failed to create features profile'
                    return internal_error(message, trace)
                if not fp_in_successful:
                    trace = traceback.format_exc()
                    fail_msg = 'error :: create_features_profile failed'
                    return internal_error(fail_msg, 'no traceback available')

        fp_details = None

        # @added 20170305  - Feature #1960: ionosphere_layers
        l_id = None
        l_details = None
        l_details_object = False
        la_details = None

        # @added 20170402 - Feature #2000: Ionosphere - validated
        validated_fp_success = False

        # added 20170908 - Feature #2056: ionosphere - disabled_features_profiles
        family_tree_fp_ids = None
        disabled_fp_success = None

        if fp_view:
            # @added 20170402 - Feature #2000: Ionosphere - validated
            validate = False
            if 'validate_fp' in request.args:
                validate_arg = request.args.get('validate_fp', False)
                if validate_arg == 'true':
                    validate = True
                    logger.info('validate - %s' % str(validate))
            if validate:
                logger.info('validating - fp_ip %s' % str(fp_id))
                try:
                    # @modified 20181013 - Feature #2430: Ionosphere validate learnt features profiles page
                    # Added the extended validate_fp parameter of id_column_name
                    # validated_fp_success, fail_msg, traceback_format_exc = validate_fp(fp_id)
                    # @modified 20190919 - Feature #3230: users DB table
                    #                      Ideas #2476: Label and relate anomalies
                    #                      Feature #2516: Add label to features profile
                    # Added user_id
                    # validated_fp_success, fail_msg, traceback_format_exc = validate_fp(fp_id, 'id')
                    validated_fp_success, fail_msg, traceback_format_exc = validate_fp(fp_id, 'id', user_id)
                    logger.info('validated fp_id - %s' % str(fp_id))
                except:
                    trace = traceback.format_exc()
                    message = 'failed to validate features profile'
                    return internal_error(message, trace)

            # added 20170908 - Feature #2056: ionosphere - disabled_features_profiles
            family_tree_fp_ids, fail_msg, traceback_format_exc = features_profile_family_tree(fp_id)
            if 'disable_fp' in request.args:
                value = request.args.get(str('disable_fp'), None)
                if int(value) > 1:
                    disable_fp = int(value)
                    logger.info('disable_fp is set to %s' % str(disable_fp))
            if disable_fp:
                logger.info('disabling fp ids - %s' % str(family_tree_fp_ids))
                disabled_fp_success, fail_msg, traceback_format_exc = disable_features_profile_family_tree(family_tree_fp_ids)

            try:
                # @modified 20170114 -  Feature #1854: Ionosphere learn - generations
                # Return the fp_details_object so that webapp can pass the parent_id and
                # generation to the templates
                # fp_details, fp_details_successful, fail_msg, traceback_format_exc = features_profile_details(fp_id)
                fp_details, fp_details_successful, fail_msg, traceback_format_exc, fp_details_object = features_profile_details(fp_id)
            except:
                # trace = traceback_format_exc
                trace = traceback.format_exc()
                message = 'failed to get features profile details'
                return internal_error(message, trace)
            if not fp_details_successful:
                trace = traceback.format_exc()
                fail_msg = 'error :: features_profile_details failed'
                return internal_error(fail_msg, trace)

            # @added 20170305  - Feature #1960: ionosphere_layers
            fp_layers_id = None
            try:
                fp_layers_id = int(fp_details_object['layers_id'])
            except:
                fp_layers_id = 0

            # @added 20190922 - Feature #2516: Add label to features profile
            try:
                fp_label = fp_details_object['label']
            except:
                fp_label = None

            # @modified 20190503 - Branch #2646: slack - linting
            # layer_details = None

            layer_details_success = False
            if fp_layers_id:
                try:
                    l_details, layer_details_success, fail_msg, traceback_format_exc, l_details_object = feature_profile_layers_detail(fp_layers_id)
                except:
                    trace = traceback.format_exc()
                    message = 'failed to get features profile layers details for id %s' % str(fp_layers_id)
                    return internal_error(message, trace)
                try:
                    la_details, layer_algorithms_success, fail_msg, traceback_format_exc, la_details_object = feature_profile_layer_alogrithms(fp_layers_id)
                    l_id = fp_layers_id
                except:
                    trace = traceback.format_exc()
                    message = 'failed to get features profile layer algorithm details for id %s' % str(fp_layers_id)
                    return internal_error(message, trace)

        valid_learning_duration = None

        # @added 20170308  - Feature #1960: ionosphere_layers - glm_images to m_app_context
        glm_images = None
        l_id_matched = None
        m_app_context = 'Analyzer'
        # @added 20170309  - Feature #1960: ionosphere_layers - i_ts_json
        i_ts_json = None
        sample_ts_json = None
        sample_i_ts_json = None

        # @added 20170331 - Task #1988: Review - Ionosphere layers - always show layers
        #                   Feature #1960: ionosphere_layers
        anomalous_timeseries = None
        f_id_matched = None
        fp_details_list = None
        f_id_created = None
        fp_generation_created = None

        try:
            # @modified 20170106 - Feature #1842: Ionosphere - Graphite now graphs
            # Added graphite_now_images gimages
            # @modified 20170107 - Feature #1852: Ionosphere - features_profile matched graphite graphs
            # Added graphite_matched_images gmimages
            # @modified 20170308 - Feature #1960: ionosphere_layers
            # Show the latest matched layers graphs as well added glm_images - graphite_layers_matched_images
            # @modified 20170309 - Feature #1960: ionosphere_layers
            # Also return the Analyzer FULL_DURATION timeseries if available in a Mirage
            # based features profile added i_ts_json
            # @added 20170331 - Task #1988: Review - Ionosphere layers - always show layers
            #                   Feature #1960: ionosphere_layers
            # Return the anomalous_timeseries as an array to sample and fp_id_matched
            # @added 20170401 - Task #1988: Review - Ionosphere layers - added fp_id_created
            # @added 20190328 - Feature #2484: FULL_DURATION feature profiles
            # Added fp_anomaly_timestamp ionosphere_echo features profiles
            mpaths, images, hdate, m_vars, ts_json, data_to_process, p_id, gimages, gmimages, times_matched, glm_images, l_id_matched, ts_fd, i_ts_json, anomalous_timeseries, f_id_matched, fp_details_list, fp_anomaly_timestamp = ionosphere_metric_data(requested_timestamp, base_name, context, fp_id)

            # @added 20170309  - Feature #1960: ionosphere_layers - i_ts_json
            # Show the last 30
            if ts_json:
                try:
                    sample_ts_json = ts_json[-30:]
                except:
                    trace = traceback.format_exc()
                    message = 'Failed to smaple ts_json'
                    return internal_error(message, trace)

            # @modified 20170331 - Task #1988: Review - Ionosphere layers - always show layers
            #                      Feature #1960: ionosphere_layers
            # Return the anomalous_timeseries as an array to sample
            # if i_ts_json:
            #     sample_i_ts_json = i_ts_json[-30:]
            if anomalous_timeseries:
                sample_i_ts_json = anomalous_timeseries[-30:]

            if fp_details_list:
                f_id_created = fp_details_list[0]
                # @modified 20170729 - Feature #1854: Ionosphere learn - generations
                # Make backwards compatible with older features profiles
                # fp_generation_created = fp_details_list[8]
                try:
                    fp_generation_created = fp_details_list[8]
                except:
                    fp_generation_created = 0

            # @added 20170120 -  Feature #1854: Ionosphere learn - generations
            # Added fp_learn parameter to allow the user to not learn the
            # use_full_duration_days so added fp_fd_days
            use_full_duration, valid_learning_duration, fp_fd_days, max_generations, max_percent_diff_from_origin = get_ionosphere_learn_details(skyline_app, base_name)

            # @added 20170104 - Feature #1842: Ionosphere - Graphite now graphs
            # Added the full_duration parameter so that the appropriate graphs can be
            # embedded for the user in the training data page
            full_duration = settings.FULL_DURATION
            full_duration_in_hours = int(full_duration / 3600)
            second_order_resolution_hours = False
            try:
                key = 'full_duration'
                value_list = [var_array[1] for var_array in m_vars if var_array[0] == key]
                m_full_duration = int(value_list[0])
                m_full_duration_in_hours = int(m_full_duration / 3600)
                if m_full_duration != full_duration:
                    second_order_resolution_hours = m_full_duration_in_hours
                    # @added 20170305  - Feature #1960: ionosphere_layers - m_app_context
                    m_app_context = 'Mirage'
            except:
                m_full_duration = False
                m_full_duration_in_hours = False
                message = 'Uh oh ... a Skyline 500 :( :: m_vars - %s' % str(m_vars)
                trace = traceback.format_exc()
                return internal_error(message, trace)

            # @added 20190330 - Feature #2484: FULL_DURATION feature profiles
            # For Ionosphere echo and adding red borders on the matched graphs
            if m_full_duration_in_hours:
                m_fd_in_hours_img_str = '%sh.png' % str(m_full_duration_in_hours)
            else:
                m_fd_in_hours_img_str = False

            # @added 20170105 - Feature #1842: Ionosphere - Graphite now graphs
            # We want to sort the images so that the Graphite image is always
            # displayed first in he training_data.html page AND we want Graphite
            # now graphs at TARGET_HOURS, 24h, 7d, 30d to inform the operator
            # about the metric
            sorted_images = sorted(images)

            # @modified 20170105 - Feature #1842: Ionosphere - Graphite now graphs
            # Added matched_count and only displaying one graph for each 10
            # minute period if there are mulitple matches in a 10 minute period
            # @modified 20170114 -  Feature #1854: Ionosphere learn - generations
            # Added parent_id and generation
            par_id = 0
            gen = 0
            # @added 20170402 - Feature #2000: Ionosphere - validated
            fp_validated = 0

            # added 20170908 - Feature #2056: ionosphere - disabled_features_profiles
            fp_enabled = False

            # @added 20190328 - Feature #2484: FULL_DURATION feature profiles
            # Added ionosphere_echo
            echo_fp_value = 0

            # @added 20190619 - Feature #2990: Add metrics id to relevant web pages
            metric_id = False

            # Determine the parent_id and generation as they were added to the
            # fp_details_object
            if fp_details:
                try:
                    par_id = int(fp_details_object['parent_id'])
                    gen = int(fp_details_object['generation'])
                    # @added 20170402 - Feature #2000: Ionosphere - validated
                    fp_validated = int(fp_details_object['validated'])
                    # added 20170908 - Feature #2056: ionosphere - disabled_features_profiles
                    if int(fp_details_object['enabled']) == 1:
                        fp_enabled = True
                    # @added 20190328 - Feature #2484: FULL_DURATION feature profiles
                    # Added ionosphere_echo
                    try:
                        echo_fp_value = int(fp_details_object['echo_fp'])
                    except:
                        pass

                    # @added 20190619 - Feature #2990: Add metrics id to relevant web pages
                    # Determine the metric_id from the fp_details_object
                    if not metric_id:
                        metric_id = int(fp_details_object['metric_id'])

                except:
                    trace = traceback.format_exc()
                    message = 'Uh oh ... a Skyline 500 :( :: failed to determine parent or generation values from the fp_details_object'
                    return internal_error(message, trace)

                # @added 20190328 - Feature #2484: FULL_DURATION feature profiles
                # Added ionosphere_echo
                echo_hdate = hdate
                if echo_fp_value == 1:
                    try:
                        # echo_hdate = datetime.datetime.utcfromtimestamp(fp_anomaly_timestamp).strftime('%Y-%m-%d %H:%M:%S')
                        echo_hdate = time.strftime('%Y-%m-%d %H:%M:%S %Z (%A)', time.localtime(int(fp_anomaly_timestamp)))
                    except:
                        trace = traceback.format_exc()
                        fail_msg = 'error :: Webapp failed to determine the echo_hdate from the fp_anomaly_timestamp'
                        logger.error(fail_msg)
                        return internal_error(fail_msg, trace)

            # @added 20170114 -  Feature #1854: Ionosphere learn - generations
            # The fp_id will be in the fp_details_object, but if this is a
            # generation zero features profile we what set
            if generation_zero:
                par_id = 0
                gen = 0

            # @added 20170122 - Feature #1876: Ionosphere - training_data learn countdown
            # Add a countdown until Ionosphere will learn
            countdown_to = False
            if requested_timestamp and valid_learning_duration:
                try:
                    request_time = int(time.time())
                    vaild_learning_timestamp = int(requested_timestamp) + int(valid_learning_duration)
                    if request_time < vaild_learning_timestamp:
                        countdown_to = time.strftime(
                            '%Y-%m-%d %H:%M:%S', time.localtime(vaild_learning_timestamp))
                except:
                    trace = traceback.format_exc()
                    message = 'Uh oh ... a Skyline 500 :( :: failed to determine parent or generation values from the fp_details_object'
                    return internal_error(message, trace)

            iono_metric = False
            if base_name:
                try:
                    ionosphere_metrics = list(REDIS_CONN.smembers('ionosphere.unique_metrics'))
                except:
                    logger.warn('warning :: Webapp could not get the ionosphere.unique_metrics list from Redis, this could be because there are none')
                metric_name = settings.FULL_NAMESPACE + str(base_name)
                if metric_name in ionosphere_metrics:
                    iono_metric = True

            # @added 20170303 - Feature #1960: ionosphere_layers
            vconds = ['<', '>', '==', '!=', '<=', '>=']

            # @modified 20190503 - Branch #2646: slack - linting
            # condition_list = ['<', '>', '==', '!=', '<=', '>=', 'in', 'not in']

            crit_types = ['value', 'time', 'day', 'from_time', 'until_time']

            fp_layers = None
            if 'fp_layer' in request.args:
                fp_layer_arg = request.args.get(str('fp_layer'), None)
                if str(fp_layer_arg) == 'true':
                    fp_layers = True
            add_fp_layers = None
            if 'add_fp_layer' in request.args:
                add_fp_layer_arg = request.args.get(str('add_fp_layer'), None)
                if str(add_fp_layer_arg) == 'true':
                    add_fp_layers = True
            new_l_algos = None
            new_l_algos_ids = None
            if add_fp_layers:
                if 'learn' in request.args:
                    value = request.args.get(str('learn'))
                    if str(value) == 'true':
                        fp_learn = True
                    if str(value) == 'True':
                        fp_learn = True
                if 'fp_id' in request.args:
                    fp_id = request.args.get(str('fp_id'))
                l_id, layer_successful, new_l_algos, new_l_algos_ids, fail_msg, trace = create_ionosphere_layers(base_name, fp_id, requested_timestamp)
                if not layer_successful:
                    return internal_error(fail_msg, trace)
                else:
                    webapp_update_slack_thread(base_name, requested_timestamp, fp_id, 'layers_created')

            # @added 20170308 - Feature #1960: ionosphere_layers
            # To present the operator with the existing layers and algorithms for the metric
            # The metric layers algoritms are required to present the user with
            # if the add_fp=true argument is passed, which if so results in the
            # local variable of create_feature_profile being set and in the
            # fp_view
            metric_layers_details = None
            metric_layers_algorithm_details = None
            # @modified 20170331 - Task #1988: Review - Ionosphere layers - always show layers
            # Set to True so they are always displayed
            # get_metric_existing_layers = False
            get_metric_existing_layers = True
            metric_lc = 0
            metric_lmc = None
            if create_feature_profile:
                get_metric_existing_layers = True
            if fp_view and fp_details:
                get_metric_existing_layers = True
            if 'add_fp' in request.args:
                get_layers_add_fp = request.args.get(str('add_fp'), None)
                if get_layers_add_fp == 'true':
                    get_metric_existing_layers = True
            if get_metric_existing_layers:
                metric_layers_details, metric_layers_algorithm_details, metric_lc, metric_lmc, mlad_successful, fail_msg, trace = metric_layers_alogrithms(base_name)
                if not mlad_successful:
                    return internal_error(fail_msg, trace)

            # @added 20170616 - Feature #2048: D1 ionosphere layer
            fp_layer_algorithms = []
            if metric_layers_algorithm_details:
                for i_layer_algorithm in metric_layers_algorithm_details:
                    try:
                        if int(i_layer_algorithm[1]) == int(l_id):
                            fp_layer_algorithms.append(i_layer_algorithm)
                    except:
                        logger.warn('warning :: Webapp could not determine layer_algorithm in metric_layers_algorithm_details')
            fp_current_layer = []
            if metric_layers_details:
                for i_layer in metric_layers_details:
                    try:
                        if int(i_layer[0]) == int(l_id):
                            fp_current_layer.append(i_layer)
                    except:
                        logger.warn('warning :: Webapp could not determine layer in metric_layers_details')

            # @added 20170617 - Feature #2054: ionosphere.save.training_data
            training_data_saved = False
            saved_td_details = False
            if save_training_data:
                logger.info('saving training data')
                try:
                    request_time = int(time.time())
                    saved_hdate = time.strftime('%Y-%m-%d %H:%M:%S %Z (%A)', time.localtime(request_time))
                    training_data_saved, saved_td_details, fail_msg, trace = save_training_data_dir(requested_timestamp, base_name, saved_td_label, saved_hdate)
                    logger.info('saved training data')
                except:
                    logger.error('error :: Webapp could not save_training_data_dir')
                    return internal_error(fail_msg, trace)
            saved_td_requested = False
            if saved_training_data:
                saved_td_requested = True
                try:
                    training_data_saved, saved_td_details, fail_msg, trace = save_training_data_dir(requested_timestamp, base_name, None, None)
                    logger.info('got saved training data details')
                except:
                    logger.error('error :: Webapp could not get saved training_data details')
                    return internal_error(fail_msg, trace)

            # @added 20170917 - Feature #1996: Ionosphere - matches page
            matched_id_resources = None
            matched_graph_image_file = None
            if matched_fp_id:
                if matched_fp_id != 'False':
                    matched_id_resources, successful, fail_msg, trace, matched_details_object, matched_graph_image_file = get_matched_id_resources(int(matched_fp_id), 'features_profile', base_name, requested_timestamp)
            if matched_layer_id:
                if matched_layer_id != 'False':
                    matched_id_resources, successful, fail_msg, trace, matched_details_object, matched_graph_image_file = get_matched_id_resources(int(matched_layer_id), 'layers', base_name, requested_timestamp)

            # @added 20180620 - Feature #2404: Ionosphere - fluid approximation
            # Added minmax scaling
            minmax = 0
            if matched_fp_id:
                minmax = int(matched_details_object['minmax'])
                logger.info('the fp match has minmax set to %s' % str(minmax))

            # @added 20190601 - Feature #3084: Ionosphere - validated matches
            # Update the DB that the match has been validated or invalidated
            validated_match_successful = None
            match_validated_db_value = None
            if matched_fp_id or matched_layer_id:
                match_validated_db_value = matched_details_object['validated']
                logger.info('the match_validated_db_value is set to %s' % str(match_validated_db_value))
                logger.info('the match_validated is set to %s' % str(match_validated))
                # Only update if the value in the DB is different from the value
                # in the argument, due to there being a difficulty in the
                # removal of the match_validation argument due to the
                # redirect_url function being applied earlier ^^ in the process.
                # Unfortunately without the redirect_url function being applied
                # this match_validation and match_validated would not work as
                # the matched context the redirect_url function is used.  Hence
                # more F.
                if int(match_validated_db_value) != int(match_validated):
                    if 'match_validation' in request.args:
                        if match_validated > 0:
                            logger.info('validating match')
                            if matched_fp_id:
                                match_id = matched_fp_id
                                validate_context = 'ionosphere_matched'
                            if matched_layer_id:
                                match_id = matched_layer_id
                                validate_context = 'ionosphere_layers_matched'
                            try:
                                # @modified 20190920 -
                                # Added user_id
                                # validated_match_successful = validate_ionosphere_match(match_id, validate_context, match_validated)
                                validated_match_successful = validate_ionosphere_match(match_id, validate_context, match_validated, user_id)
                                # @added 20190921 - Feature #3234: Ionosphere - related matches vaildation
                                # TODO - here related matches will also be validated
                                logger.info('validated match')
                                match_validated_db_value = match_validated
                            except:
                                trace = traceback.format_exc()
                                fail_msg = 'error :: Webapp error with search_ionosphere'
                                logger.error(fail_msg)
                                return internal_error(fail_msg, trace)

            # @added 20180921 - Feature #2558: Ionosphere - fluid approximation - approximately_close on layers
            approx_close = 0
            if matched_layer_id:
                approx_close = int(matched_details_object['approx_close'])
                logger.info('the layers match has approx_close set to %s' % str(approx_close))

            # @added 20180414 - Branch #2270: luminosity
            # Add correlations to features_profile and training_data pages if a
            # panorama_anomaly_id is present
            correlations = False
            correlations_with_graph_links = []
            if p_id:
                try:
                    correlations, fail_msg, trace = get_correlations(skyline_app, p_id)
                except:
                    trace = traceback.format_exc()
                    fail_msg = 'error :: Webapp error with search_ionosphere'
                    logger.error(fail_msg)
                    return internal_error(fail_msg, trace)
                if correlations:
                    # @added 20180723 - Feature #2470: Correlations Graphite graph links
                    #                   Branch #2270: luminosity
                    # Added Graphite graph links to Correlations block
                    for metric_name, coefficient, shifted, shifted_coefficient in correlations:
                        from_timestamp = int(requested_timestamp) - m_full_duration
                        graphite_from = datetime.datetime.fromtimestamp(int(from_timestamp)).strftime('%H:%M_%Y%m%d')
                        graphite_until = datetime.datetime.fromtimestamp(int(requested_timestamp)).strftime('%H:%M_%Y%m%d')
                        unencoded_graph_title = '%s\ncorrelated with anomaly id %s' % (
                            metric_name, str(p_id))
                        graph_title_string = quote(unencoded_graph_title, safe='')
                        graph_title = '&title=%s' % graph_title_string
                        if settings.GRAPHITE_PORT != '':
                            # @modified 20190520 - Branch #3002: docker
                            # correlation_graphite_link = '%s://%s:%s/render/?from=%s&until=%s&target=cactiStyle(%s)%s%s&colorList=blue' % (settings.GRAPHITE_PROTOCOL, settings.GRAPHITE_HOST, settings.GRAPHITE_PORT, str(graphite_from), str(graphite_until), metric_name, settings.GRAPHITE_GRAPH_SETTINGS, graph_title)
                            correlation_graphite_link = '%s://%s:%s/%s/?from=%s&until=%s&target=cactiStyle(%s)%s%s&colorList=blue' % (
                                settings.GRAPHITE_PROTOCOL, settings.GRAPHITE_HOST,
                                settings.GRAPHITE_PORT, GRAPHITE_RENDER_URI,
                                str(graphite_from), str(graphite_until), metric_name,
                                settings.GRAPHITE_GRAPH_SETTINGS, graph_title)
                        else:
                            # @modified 20190520 - Branch #3002: docker
                            # correlation_graphite_link = '%s://%s/render/?from=%s&until=%starget=cactiStyle(%s)%s%s&colorList=blue' % (settings.GRAPHITE_PROTOCOL, settings.GRAPHITE_HOST, str(graphite_from), str(graphite_until), metric_name, settings.GRAPHITE_GRAPH_SETTINGS, graph_title)
                            correlation_graphite_link = '%s://%s/%s/?from=%s&until=%starget=cactiStyle(%s)%s%s&colorList=blue' % (
                                settings.GRAPHITE_PROTOCOL, settings.GRAPHITE_HOST,
                                GRAPHITE_RENDER_URI, str(graphite_from),
                                str(graphite_until), metric_name,
                                settings.GRAPHITE_GRAPH_SETTINGS, graph_title)
                        correlations_with_graph_links.append([metric_name, coefficient, shifted, shifted_coefficient, str(correlation_graphite_link)])

            # @added 20190510 - Feature #2990: Add metrics id to relevant web pages
            # By this point in the request the previous function calls will have
            # populated memcache with the metric details
            if not metric_id:
                metric_id = 0
                try:
                    cache_key = 'panorama.mysql_ids.metrics.metric.%s' % base_name
                    metric_id_msg_pack = None
                    metric_id_msg_pack = REDIS_CONN.get(cache_key)
                    if metric_id_msg_pack:
                        unpacker = Unpacker(use_list=False)
                        unpacker.feed(metric_id_msg_pack)
                        metric_id = [item for item in unpacker][0]
                        logger.info('metrics id is %s from Redis key -%s' % (str(metric_id), cache_key))
                    else:
                        logger.info('Webapp could not get metric id from Redis key - %s' % cache_key)
                except:
                    logger.info(traceback.format_exc())
                    logger.error('error :: Webapp could not get metric id from Redis key - %s' % cache_key)
            else:
                logger.info('metrics id is %s' % str(metric_id))

            # @added 20190502 - Branch #2646: slack
            if context == 'training_data':
                update_slack = True
                # Do not update if a fp_id is present
                if fp_id:
                    update_slack = False
                # Do not update slack when extract features is run
                if calculate_features:
                    update_slack = False
                if update_slack:
                    slack_updated = webapp_update_slack_thread(base_name, requested_timestamp, None, 'training_data_viewed')
                    logger.info('slack_updated for training_data_viewed %s' % str(slack_updated))

            return render_template(
                'ionosphere.html', timestamp=requested_timestamp,
                for_metric=base_name, metric_vars=m_vars, metric_files=mpaths,
                metric_images=sorted_images, human_date=hdate, timeseries=ts_json,
                data_ok=data_to_process, td_files=mpaths,
                panorama_anomaly_id=p_id, graphite_url=graph_url,
                extracted_features=features, calc_time=f_calc,
                features_profile_id=fp_id, features_profile_exists=fp_exists,
                fp_view=fp_view_on, features_profile_details=fp_details,
                redis_full_duration=full_duration,
                redis_full_duration_in_hours=full_duration_in_hours,
                metric_full_duration=m_full_duration,
                metric_full_duration_in_hours=m_full_duration_in_hours,
                metric_second_order_resolution_hours=second_order_resolution_hours,
                tsfresh_version=TSFRESH_VERSION, graphite_now_images=gimages,
                graphite_matched_images=gmimages, matched_count=times_matched,
                parent_id=par_id, generation=gen, learn=fp_learn,
                use_full_duration_days=fp_fd_days, countdown=countdown_to,
                ionosphere_metric=iono_metric, value_condition_list=vconds,
                criteria_types=crit_types, fp_layer=fp_layers,
                layer_id=l_id, layers_algorithms=new_l_algos,
                layers_algorithms_ids=new_l_algos_ids,
                layer_details=l_details,
                layer_details_object=l_details_object,
                layer_algorithms_details=la_details,
                existing_layers=metric_layers_details,
                existing_algorithms=metric_layers_algorithm_details,
                metric_layers_count=metric_lc,
                metric_layers_matched_count=metric_lmc,
                graphite_layers_matched_images=glm_images,
                layers_id_matched=l_id_matched, ts_full_duration=ts_fd,
                app_context=m_app_context, ionosphere_json=i_ts_json,
                baseline_fd=full_duration, last_ts_json=sample_ts_json,
                last_i_ts_json=sample_i_ts_json, layers_updated=layers_updated,
                fp_id_matched=f_id_matched, fp_id_created=f_id_created,
                fp_generation=fp_generation_created, validated=fp_validated,
                validated_fp_successful=validated_fp_success,
                profile_layer_algorithms=fp_layer_algorithms,
                current_layer=fp_current_layer,
                save_metric_td=save_training_data,
                saved_metric_td_label=saved_td_label,
                saved_metric_td=saved_training_data,
                metric_training_data_saved=training_data_saved,
                saved_metric_td_requested=saved_td_requested,
                saved_metric_td_details=saved_td_details,
                profile_enabled=fp_enabled, disable_feature_profile=disable_fp,
                disabled_fp_successful=disabled_fp_success,
                family_tree_ids=family_tree_fp_ids,
                matched_fp_id=matched_fp_id, matched_layer_id=matched_layer_id,
                matched_id_resources=matched_id_resources,
                matched_graph_image_file=matched_graph_image_file,
                # @added 20180620 - Feature #2404: Ionosphere - fluid approximation
                # Added minmax scaling
                minmax=minmax,
                correlations=correlations,
                # @added 20180723 - Feature #2470: Correlations Graphite graph links
                #                   Branch #2270: luminosity
                # Added Graphite graph links to the Correlations block in
                # the correlations.html and training_data.html templates
                correlations_with_graph_links=correlations_with_graph_links,
                matched_from_datetime=matched_from_datetime,
                # @added 20180921 - Feature #2558: Ionosphere - fluid approximation - approximately_close on layers
                approx_close=approx_close,
                # @added 20190328 - Feature #2484: FULL_DURATION feature profiles
                # Added ionosphere_echo
                echo_fp=echo_fp_value, echo_human_date=echo_hdate,
                metric_full_duration_in_hours_image_str=m_fd_in_hours_img_str,
                # @added 20190510 - Feature #2990: Add metrics id to relevant web pages
                metric_id=metric_id,
                # @added 20190601 - Feature #3084: Ionosphere - validated matches
                match_validated=match_validated,
                match_validated_db_value=match_validated_db_value,
                validated_match_successful=validated_match_successful,
                # @added 20190922 - Feature #2516: Add label to features profile
                fp_label=fp_label,
                version=skyline_version, duration=(time.time() - start),
                print_debug=debug_on), 200
        except:
            message = 'Uh oh ... a Skyline 500 :('
            trace = traceback.format_exc()
            return internal_error(message, trace)

    try:
        message = 'Unknown request'
        return render_template(
            'ionosphere.html', display_message=message,
            version=skyline_version, duration=(time.time() - start),
            print_debug=debug_on), 200
    except:
        message = 'Uh oh ... a Skyline 500 :('
        trace = traceback.format_exc()
        return internal_error(message, trace)


@app.route('/ionosphere_images')
def ionosphere_images():

    request_args_present = False
    try:
        request_args_len = len(request.args)
        request_args_present = True
    except:
        request_args_len = 0
        logger.error('error :: request arguments have no length - %s' % str(request_args_len))

    IONOSPHERE_REQUEST_ARGS = ['image']

    if request_args_present:
        for i in request.args:
            key = str(i)
            if key not in IONOSPHERE_REQUEST_ARGS:
                logger.error('error :: invalid request argument - %s=%s' % (key, str(i)))
                # @modified 20190524 - Branch #3002: docker
                # Return data
                # return 'Bad Request', 400
                error_string = 'error :: invalid request argument - %s=%s' % (key, str(i))
                logger.error(error_string)
                resp = json.dumps(
                    {'400 Bad Request': error_string})
                return flask_escape(resp), 400

            value = request.args.get(key, None)
            logger.info('request argument - %s=%s' % (key, str(value)))

            if key == 'image':
                filename = str(value)
                if os.path.isfile(filename):
                    try:
                        return send_file(filename, mimetype='image/png')
                    except:
                        message = 'Uh oh ... a Skyline 500 :( - could not return %s' % filename
                        trace = traceback.format_exc()
                        return internal_error(message, trace)
                else:
                    image_404_path = 'webapp/static/images/skyline.ionosphere.image.404.png'
                    filename = path.abspath(
                        path.join(path.dirname(__file__), '..', image_404_path))
                    try:
                        return send_file(filename, mimetype='image/png')
                    except:
                        message = 'Uh oh ... a Skyline 500 :( - could not return %s' % filename
                        trace = traceback.format_exc()
                        return internal_error(message, trace)

    return 'Bad Request', 400

# @added 20170102 - Feature #1838: utilites - ALERTS matcher
#                   Branch #922: ionosphere
#                   Task #1658: Patterning Skyline Ionosphere
# Added utilities TODO


@app.route("/utilities")
@requires_auth
def utilities():
    # start = time.time()
    try:
        return render_template('utilities.html'), 200
    except:
        error_string = traceback.format_exc()
        logger.error('error :: failed to render utilities.html: %s' % str(error_string))
        return 'Uh oh ... a Skyline 500 :(', 500


# @added 20160703 - Feature #1464: Webapp Redis browser
# A port of Marian Steinbach's rebrow - https://github.com/marians/rebrow
# Description of info keys
# TODO: to be continued.
serverinfo_meta = {
    'aof_current_rewrite_time_sec': "Duration of the on-going <abbr title='Append-Only File'>AOF</abbr> rewrite operation if any",
    'aof_enabled': "Flag indicating <abbr title='Append-Only File'>AOF</abbr> logging is activated",
    'aof_last_bgrewrite_status': "Status of the last <abbr title='Append-Only File'>AOF</abbr> rewrite operation",
    'aof_last_rewrite_time_sec': "Duration of the last <abbr title='Append-Only File'>AOF</abbr> rewrite operation in seconds",
    'aof_last_write_status': "Status of last <abbr title='Append-Only File'>AOF</abbr> write operation",
    'aof_rewrite_in_progress': "Flag indicating a <abbr title='Append-Only File'>AOF</abbr> rewrite operation is on-going",
    'aof_rewrite_scheduled': "Flag indicating an <abbr title='Append-Only File'>AOF</abbr> rewrite operation will be scheduled once the on-going RDB save is complete",
    'arch_bits': 'Architecture (32 or 64 bits)',
    'blocked_clients': 'Number of clients pending on a blocking call (BLPOP, BRPOP, BRPOPLPUSH)',
    'client_biggest_input_buf': 'biggest input buffer among current client connections',
    'client_longest_output_list': None,
    'cmdstat_client': 'Statistics for the client command',
    'cmdstat_config': 'Statistics for the config command',
    'cmdstat_dbsize': 'Statistics for the dbsize command',
    'cmdstat_del': 'Statistics for the del command',
    'cmdstat_dump': 'Statistics for the dump command',
    'cmdstat_expire': 'Statistics for the expire command',
    'cmdstat_flushall': 'Statistics for the flushall command',
    'cmdstat_get': 'Statistics for the get command',
    'cmdstat_hgetall': 'Statistics for the hgetall command',
    'cmdstat_hkeys': 'Statistics for the hkeys command',
    'cmdstat_hmset': 'Statistics for the hmset command',
    'cmdstat_info': 'Statistics for the info command',
    'cmdstat_keys': 'Statistics for the keys command',
    'cmdstat_llen': 'Statistics for the llen command',
    'cmdstat_ping': 'Statistics for the ping command',
    'cmdstat_psubscribe': 'Statistics for the psubscribe command',
    'cmdstat_pttl': 'Statistics for the pttl command',
    'cmdstat_sadd': 'Statistics for the sadd command',
    'cmdstat_scan': 'Statistics for the scan command',
    'cmdstat_select': 'Statistics for the select command',
    'cmdstat_set': 'Statistics for the set command',
    'cmdstat_smembers': 'Statistics for the smembers command',
    'cmdstat_sscan': 'Statistics for the sscan command',
    'cmdstat_ttl': 'Statistics for the ttl command',
    'cmdstat_type': 'Statistics for the type command',
    'cmdstat_zadd': 'Statistics for the zadd command',
    'cmdstat_zcard': 'Statistics for the zcard command',
    'cmdstat_zrange': 'Statistics for the zrange command',
    'cmdstat_zremrangebyrank': 'Statistics for the zremrangebyrank command',
    'cmdstat_zrevrange': 'Statistics for the zrevrange command',
    'cmdstat_zscan': 'Statistics for the zscan command',
    'config_file': None,
    'connected_clients': None,
    'connected_slaves': None,
    'db0': None,
    'evicted_keys': None,
    'expired_keys': None,
    'gcc_version': None,
    'hz': None,
    'instantaneous_ops_per_sec': None,
    'keyspace_hits': None,
    'keyspace_misses': None,
    'latest_fork_usec': None,
    'loading': None,
    'lru_clock': None,
    'master_repl_offset': None,
    'mem_allocator': None,
    'mem_fragmentation_ratio': None,
    'multiplexing_api': None,
    'os': None,
    'process_id': None,
    'pubsub_channels': None,
    'pubsub_patterns': None,
    'rdb_bgsave_in_progress': None,
    'rdb_changes_since_last_save': None,
    'rdb_current_bgsave_time_sec': None,
    'rdb_last_bgsave_status': None,
    'rdb_last_bgsave_time_sec': None,
    'rdb_last_save_time': None,
    'redis_build_id': None,
    'redis_git_dirty': None,
    'redis_git_sha1': None,
    'redis_mode': None,
    'redis_version': None,
    'rejected_connections': None,
    'repl_backlog_active': None,
    'repl_backlog_first_byte_offset': None,
    'repl_backlog_histlen': None,
    'repl_backlog_size': None,
    'role': None,
    'run_id': None,
    'sync_full': None,
    'sync_partial_err': None,
    'sync_partial_ok': None,
    'tcp_port': None,
    'total_commands_processed': None,
    'total_connections_received': None,
    'uptime_in_days': None,
    'uptime_in_seconds': None,
    'used_cpu_sys': None,
    'used_cpu_sys_children': None,
    'used_cpu_user': None,
    'used_cpu_user_children': None,
    'used_memory': None,
    'used_memory_human': None,
    'used_memory_lua': None,
    'used_memory_peak': None,
    'used_memory_peak_human': None,
    'used_memory_rss': None
}


# @added 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
# Added auth to rebrow as per https://github.com/marians/rebrow/pull/20 by
# elky84
def get_redis(host, port, db, password):
    if password == "":
        # @modified 20190517 - Branch #3002: docker
        # Allow rebrow to connect to Redis on the socket too
        if host == 'unix_socket':
            return redis.StrictRedis(unix_socket_path=settings.REDIS_SOCKET_PATH, db=db)
        else:
            return redis.StrictRedis(host=host, port=port, db=db)
    else:
        if host == 'unix_socket':
            return redis.StrictRedis(password=settings.REDIS_PASSWORD, unix_socket_path=settings.REDIS_SOCKET_PATH, db=db)
        else:
            return redis.StrictRedis(host=host, port=port, db=db, password=password)


# @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
# Added token, client_id and salt to replace password parameter and determining
# client protocol
def get_client_details():
    """
    Gets the first X-Forwarded-For address and sets as the IP address.
    Gets the client_id by simply using a md5 hash of the client IP address
    and user agent.
    Determines whether the request was proxied.
    Determines the client protocol.

    :return: client_id, protocol, proxied, salt
    :rtype: str, str, boolean, str

    """
    proxied = False
    if request.headers.getlist('X-Forwarded-For'):
        client_ip = str(request.headers.getlist('X-Forwarded-For')[0])
        logger.info('rebrow access :: client ip set from X-Forwarded-For[0] to %s' % (str(client_ip)))
        proxied = True
    else:
        client_ip = str(request.remote_addr)
        logger.info('rebrow access :: client ip set from remote_addr to %s, no X-Forwarded-For header was found' % (str(client_ip)))
    client_user_agent = request.headers.get('User-Agent')
    logger.info('rebrow access :: %s client_user_agent set to %s' % (str(client_ip), str(client_user_agent)))
    client_id = '%s_%s' % (client_ip, client_user_agent)
    if python_version == 2:
        client_id = hashlib.md5(client_id).hexdigest()
    else:
        client_id = hashlib.md5(client_id.encode('utf-8')).hexdigest()
    logger.info('rebrow access :: %s has client_id %s' % (str(client_ip), str(client_id)))

    if request.headers.getlist('X-Forwarded-Proto'):
        protocol_list = request.headers.getlist('X-Forwarded-Proto')
        protocol = str(protocol_list[0])
        logger.info('rebrow access :: protocol for %s was set from X-Forwarded-Proto to %s' % (client_ip, str(protocol)))
    else:
        protocol = 'unknown'
        logger.info('rebrow access :: protocol for %s was not set from X-Forwarded-Proto to %s' % (client_ip, str(protocol)))

    if not proxied:
        logger.info('rebrow access :: Skyline is not set up correctly, the expected X-Forwarded-For header was not found')

    return client_id, protocol, proxied


def decode_token(client_id):
    """
    Use the app.secret, client_id and salt to decode the token JWT encoded
    payload and determine the Redis password.

    :param client_id: the client_id string
    :type client_id: str
    :return: token, decoded_redis_password, fail_msg, trace
    :rtype: str, str, str, str

    """
    fail_msg = False
    trace = False
    token = False
    logger.info('decode_token for client_id - %s' % str(client_id))

    if not request.args.getlist('token'):
        fail_msg = 'No token url parameter was passed, please log into Redis again through rebrow'
    else:
        token = request.args.get('token', type=str)
        logger.info('token found in request.args - %s' % str(token))

    if not token:
        client_id, protocol, proxied = get_client_details()
        fail_msg = 'No token url parameter was passed, please log into Redis again through rebrow'
        trace = 'False'

    client_token_data = False
    if token:
        try:
            if settings.REDIS_PASSWORD:
                redis_conn = redis.StrictRedis(password=settings.REDIS_PASSWORD, unix_socket_path=settings.REDIS_SOCKET_PATH)
            else:
                redis_conn = redis.StrictRedis(unix_socket_path=settings.REDIS_SOCKET_PATH)
            key = 'rebrow.token.%s' % token
            client_token_data = redis_conn.get(key)
        except:
            trace = traceback.format_exc()
            fail_msg = 'Failed to get client_token_data from Redis key - %s' % key
            client_token_data = False
            token = False

    client_id_match = False
    if client_token_data is not None:
        logger.info('client_token_data retrieved from Redis - %s' % str(client_token_data))
        try:
            client_data = literal_eval(client_token_data)
            logger.info('client_token_data - %s' % str(client_token_data))
            client_data_client_id = str(client_data[0])
            logger.info('client_data_client_id - %s' % str(client_data_client_id))
        except:
            trace = traceback.format_exc()
            logger.error('%s' % trace)
            err_msg = 'error :: failed to get client data from Redis key'
            logger.error('%s' % err_msg)
            fail_msg = 'Invalid token. Please log into Redis through rebrow again.'
            client_data_client_id = False

        if client_data_client_id != client_id:
            logger.error(
                'rebrow access :: error :: the client_id does not match the client_id of the token - %s - %s' %
                (str(client_data_client_id), str(client_id)))
            try:
                if settings.REDIS_PASSWORD:
                    redis_conn = redis.StrictRedis(password=settings.REDIS_PASSWORD, unix_socket_path=settings.REDIS_SOCKET_PATH)
                else:
                    redis_conn = redis.StrictRedis(unix_socket_path=settings.REDIS_SOCKET_PATH)
                key = 'rebrow.token.%s' % token
                redis_conn.delete(key)
                logger.info('due to possible attempt at unauthorised use of the token, deleted the Redis key - %s' % str(key))
            except:
                pass
            fail_msg = 'The request data did not match the token data, due to possible attempt at unauthorised use of the token it has been deleted.'
            trace = 'this was a dodgy request'
            token = False
        else:
            client_id_match = True
    else:
        fail_msg = 'Invalid token, there was no data found associated with the token, it has probably expired.  Please log into Redis again through rebrow'
        trace = client_token_data
        token = False

    client_data_salt = False
    client_data_jwt_payload = False
    if client_id_match:
        client_data_salt = str(client_data[1])
        client_data_jwt_payload = str(client_data[2])

    decoded_redis_password = False
    if client_data_salt and client_data_jwt_payload:
        try:
            jwt_secret = '%s.%s.%s' % (app.secret_key, client_id, client_data_salt)
            jwt_decoded_dict = jwt.decode(client_data_jwt_payload, jwt_secret, algorithms=['HS256'])
            jwt_decoded_redis_password = str(jwt_decoded_dict['auth'])
            decoded_redis_password = jwt_decoded_redis_password
        except:
            trace = traceback.format_exc()
            logger.error('%s' % trace)
            err_msg = 'error :: failed to decode the JWT token with the salt and client_id'
            logger.error('%s' % err_msg)
            fail_msg = 'failed to decode the JWT token with the salt and client_id. Please log into rebrow again.'
            token = False

    return token, decoded_redis_password, fail_msg, trace


@app.route('/rebrow', methods=['GET', 'POST'])
@requires_auth
# def login():
def rebrow():
    """
    Start page
    """
    if request.method == 'POST':
        # TODO: test connection, handle failures
        host = str(request.form['host'])
        port = int(request.form['port'])
        db = int(request.form['db'])
        # @modified 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
        # Added auth to rebrow as per https://github.com/marians/rebrow/pull/20 by
        # elky84
        # url = url_for('rebrow_server_db', host=host, port=port, db=db)
        password = str(request.form['password'])

        # @added 20180529 - Feature #2378: Add redis auth to Skyline and rebrow
        token_valid_for = int(request.form['token_valid_for'])
        if token_valid_for > 3600:
            token_valid_for = 3600
        if token_valid_for < 30:
            token_valid_for = 30

        # @added 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
        # Added auth to rebrow as per https://github.com/marians/rebrow/pull/20 by
        # elky84 and add encryption to the password URL parameter trying to use
        # pycrypto/pycryptodome to encode it, but no, used PyJWT instead
        # padded_password = password.rjust(32)
        # secret_key = '1234567890123456' # create new & store somewhere safe
        # cipher = AES.new(app.secret_key,AES.MODE_ECB) # never use ECB in strong systems obviously
        # encoded = base64.b64encode(cipher.encrypt(padded_password))

        # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
        # Added client_id, token and salt
        salt = str(uuid.uuid4())
        client_id, protocol, proxied = get_client_details()

        # @added 20180526 - Feature #2378: Add redis auth to Skyline and rebrow
        # Use pyjwt - JSON Web Token implementation to encode the password and
        # pass a token in the URL password parameter, the password in the POST
        # data should be encrypted via the reverse proxy SSL endpoint
        # encoded = jwt.encode({'some': 'payload'}, 'secret', algorithm='HS256')
        # jwt.decode(encoded, 'secret', algorithms=['HS256'])
        # {'some': 'payload'}
        try:
            jwt_secret = '%s.%s.%s' % (app.secret_key, client_id, salt)
            jwt_encoded_payload = jwt.encode({'auth': str(password)}, jwt_secret, algorithm='HS256')
        except:
            message = 'Failed to create set jwt_encoded_payload for %s' % client_id
            trace = traceback.format_exc()
            return internal_error(message, trace)

        # HERE WE WANT TO PUT THIS INTO REDIS with a TTL key and give the key
        # a salt and have the client use that as their token
        client_token = str(uuid.uuid4())
        logger.info('rebrow access :: generated client_token %s for client_id %s' % (client_token, client_id))
        try:
            if settings.REDIS_PASSWORD:
                redis_conn = redis.StrictRedis(password=settings.REDIS_PASSWORD, unix_socket_path=settings.REDIS_SOCKET_PATH)
            else:
                redis_conn = redis.StrictRedis(unix_socket_path=settings.REDIS_SOCKET_PATH)
            key = 'rebrow.token.%s' % client_token
            value = '[\'%s\',\'%s\',\'%s\']' % (client_id, salt, jwt_encoded_payload)
            redis_conn.setex(key, token_valid_for, value)
            logger.info('rebrow access :: set Redis key - %s' % (key))
        except:
            message = 'Failed to set Redis key - %s' % key
            trace = traceback.format_exc()
            return internal_error(message, trace)

        # @modified 20180526 - Feature #2378: Add redis auth to Skyline and rebrow
        # Change password parameter to token parameter
        # url = url_for("rebrow_server_db", host=host, port=port, db=db, password=password)
        url = url_for(
            "rebrow_server_db", host=host, port=port, db=db, token=client_token)
        return redirect(url)
    else:
        start = time.time()

        # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
        # Added client_id
        client_id, protocol, proxied = get_client_details()

        # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
        # Added client message to give relevant messages on the login page
        client_message = False

        # @added 20190519 - Branch #3002: docker
        display_redis_password = False
        host_input_value = 'localhost'
        rebrow_redis_password = False
        try:
            running_on_docker = settings.DOCKER
        except:
            running_on_docker = False
        if running_on_docker:
            host_input_value = 'unix_socket'
            try:
                display_redis_password = settings.DOCKER_DISPLAY_REDIS_PASSWORD_IN_REBROW
            except:
                display_redis_password = False
            if display_redis_password:
                rebrow_redis_password = settings.REDIS_PASSWORD

        return render_template(
            'rebrow_login.html',
            # @modified 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
            # Change password parameter to token parameter and added protocol,
            # proxied
            # redis_password=redis_password,
            protocol=protocol, proxied=proxied, client_message=client_message,
            version=skyline_version,
            # @added 20190519 - Branch #3002: docker
            running_on_docker=running_on_docker,
            rebrow_redis_password=rebrow_redis_password,
            display_redis_password=display_redis_password,
            host_input_value=host_input_value,
            duration=(time.time() - start))


@app.route("/rebrow_server_db/<host>:<int:port>/<int:db>/")
@requires_auth
def rebrow_server_db(host, port, db):
    """
    List all databases and show info on server
    """
    start = time.time()
    # @modified 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
    # r = redis.StrictRedis(host=host, port=port, db=0)
    # @modified 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
    # Use client_id and JWT token
    # password = False
    # url_password = False
    # if request.args.getlist('password'):
    #     password = request.args.get('password', default='', type=str)
    #     url_password = quote(password, safe='')
    # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
    # Added client_id and token
    client_id, protocol, proxied = get_client_details()
    token, redis_password, fail_msg, trace = decode_token(client_id)
    if not token:
        if fail_msg:
            return internal_error(fail_msg, trace)

    try:
        r = get_redis(host, port, db, redis_password)
    except:
        logger.error(traceback.format_exc())
        logger.error('error :: rebrow access :: failed to login to Redis with token')

    try:
        info = r.info('all')
    except:
        message = 'Failed to get INFO all from Redis, this could be an issue with the Redis password you entered.'
        trace = traceback.format_exc()
        return internal_error(message, trace)

    dbsize = r.dbsize()
    return render_template(
        'rebrow_server_db.html',
        host=host,
        port=port,
        db=db,
        # @added 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
        # password=password,
        # url_password=url_password,
        # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
        token=token,
        info=info,
        dbsize=dbsize,
        serverinfo_meta=serverinfo_meta,
        version=skyline_version,
        duration=(time.time() - start))


@app.route("/rebrow_keys/<host>:<int:port>/<int:db>/keys/", methods=['GET', 'POST'])
@requires_auth
def rebrow_keys(host, port, db):
    """
    List keys for one database
    """
    start = time.time()
    # @modified 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
    # r = redis.StrictRedis(host=host, port=port, db=db)
    # @modified 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
    # password = request.args.get('password', default='', type=str)
    # url_password = quote(password, safe='')
    # r = get_redis(host, port, db, password)
    # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
    # Added client_id and token
    client_id, protocol, proxied = get_client_details()
    token, redis_password, fail_msg, trace = decode_token(client_id)
    if not token:
        if fail_msg:
            return internal_error(fail_msg, trace)

    try:
        r = get_redis(host, port, db, redis_password)
    except:
        logger.error(traceback.format_exc())
        logger.error('error :: rebrow access :: failed to login to Redis with token')

    if request.method == 'POST':
        action = request.form['action']
        app.logger.debug(action)
        if action == 'delkey':
            if request.form['key'] is not None:
                try:
                    result = r.delete(request.form['key'])
                except:
                    message = 'Failed to delete Redis key - %s' % str(request.form['key'])
                    trace = traceback.format_exc()
                    return internal_error(message, trace)
                if result == 1:
                    flash('Key %s has been deleted.' % request.form['key'], category='info')
                else:
                    flash('Key %s could not be deleted.' % request.form['key'], category='error')
        return redirect(request.url)
    else:
        offset = int(request.args.get('offset', '0'))
        # @modified 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
        # List more keys per page
        # perpage = int(request.args.get('perpage', '10'))
        perpage = int(request.args.get('perpage', '50'))
        pattern = request.args.get('pattern', '*')
        try:
            dbsize = r.dbsize()
        except:
            message = 'Failed to determine Redis dbsize'
            trace = traceback.format_exc()
            return internal_error(message, trace)

        keys = sorted(r.keys(pattern))
        limited_keys = keys[offset:(perpage + offset)]
        types = {}
        for key in limited_keys:
            types[key] = r.type(key)
        return render_template(
            'rebrow_keys.html',
            host=host,
            port=port,
            db=db,
            # @added 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
            # password=password,
            # url_password=url_password,
            # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
            token=token,
            dbsize=dbsize,
            keys=limited_keys,
            types=types,
            offset=offset,
            perpage=perpage,
            pattern=pattern,
            num_keys=len(keys),
            version=skyline_version,
            duration=(time.time() - start))


@app.route("/rebrow_key/<host>:<int:port>/<int:db>/keys/<key>/")
@requires_auth
def rebrow_key(host, port, db, key):
    """
    Show a specific key.
    key is expected to be URL-safe base64 encoded
    """
    # @added 20160703 - Feature #1464: Webapp Redis browser
    # metrics encoded with msgpack
    # @modified 20190503 - Branch #2646: slack - linting
    # original_key not used
    # original_key = key

    msg_pack_key = False
    # if key.startswith('metrics.'):
    #     msg_packed_key = True
    key = base64.urlsafe_b64decode(key.encode('utf8'))
    start = time.time()
    # @modified 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
    # r = redis.StrictRedis(host=host, port=port, db=db)
    # @modified 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
    # Use client_id and token
    # password = request.args.get('password', default='', type=str)
    # url_password = quote(password, safe='')
    # r = get_redis(host, port, db, password)
    # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
    # Added client_id and token
    client_id, protocol, proxied = get_client_details()
    token, redis_password, fail_msg, trace = decode_token(client_id)
    if not token:
        if fail_msg:
            return internal_error(fail_msg, trace)

    try:
        r = get_redis(host, port, db, redis_password)
    except:
        logger.error(traceback.format_exc())
        logger.error('error :: rebrow access :: failed to login to Redis with token')

    try:
        dump = r.dump(key)
    except:
        message = 'Failed to dump Redis key - %s' % str(key)
        trace = traceback.format_exc()
        return internal_error(message, trace)

    if dump is None:
        abort(404)
    # if t is None:
    #    abort(404)
    size = len(dump)
    # @modified 20170809 - Bug #2136: Analyzer stalling on no metrics
    # Added except to all del methods to prevent stalling if any object does
    # not exist
    try:
        del dump
    except:
        logger.error('error :: failed to del dump')
    t = r.type(key)
    ttl = r.pttl(key)
    if t == 'string':
        # @modified 20160703 - Feature #1464: Webapp Redis browser
        # metrics encoded with msgpack
        # val = r.get(key)
        try:
            val = r.get(key)
        except:
            abort(404)
        test_string = all(c in string.printable for c in val)
        # @added 20170920 - Bug #2166: panorama incorrect mysql_id cache keys
        # There are SOME cache key msgpack values that DO == string.printable
        # for example [73] msgpacks to I
        # panorama.mysql_ids will always be msgpack
        if 'panorama.mysql_ids' in str(key):
            test_string = False
        if not test_string:
            raw_result = r.get(key)
            unpacker = Unpacker(use_list=False)
            unpacker.feed(raw_result)
            val = list(unpacker)
            msg_pack_key = True
    elif t == 'list':
        val = r.lrange(key, 0, -1)
    elif t == 'hash':
        val = r.hgetall(key)
    elif t == 'set':
        val = r.smembers(key)
    elif t == 'zset':
        val = r.zrange(key, 0, -1, withscores=True)
    return render_template(
        'rebrow_key.html',
        host=host,
        port=port,
        db=db,
        # @added 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
        # password=password,
        # url_password=url_password,
        # @added 20180527 - Feature #2378: Add redis auth to Skyline and rebrow
        token=token,
        key=key,
        value=val,
        type=t,
        size=size,
        ttl=ttl / 1000.0,
        now=datetime.datetime.utcnow(),
        expiration=datetime.datetime.utcnow() + timedelta(seconds=ttl / 1000.0),
        version=skyline_version,
        duration=(time.time() - start),
        msg_packed_key=msg_pack_key)


@app.template_filter('urlsafe_base64')
def urlsafe_base64_encode(s):
    # @modified 20180520 - Feature #2378: Add redis auth to Skyline and rebrow
    # if type(s) == 'Markup':
    #     s = s.unescape()
    if isinstance(s, Markup):
        s = s.unescape()
    elif isinstance(s, bytes):
        s = s.decode('utf-8')
    s = s.encode('utf8')
    s = base64.urlsafe_b64encode(s)
    return Markup(s)
# END rebrow


class App():
    def __init__(self):
        self.stdin_path = '/dev/null'
        self.stdout_path = '%s/%s.log' % (settings.LOG_PATH, skyline_app)
        self.stderr_path = '%s/%s.log' % (settings.LOG_PATH, skyline_app)
        self.pidfile_path = '%s/%s.pid' % (settings.PID_PATH, skyline_app)
        self.pidfile_timeout = 5

    def run(self):

        # Log management to prevent overwriting
        # Allow the bin/<skyline_app>.d to manage the log
        if os.path.isfile(skyline_app_logwait):
            try:
                os_remove(skyline_app_logwait)
            except OSError:
                logger.error('error - failed to remove %s, continuing' % skyline_app_logwait)
                pass

        now = time.time()
#        log_wait_for = now + 5
        log_wait_for = now + 1
        while now < log_wait_for:
            if os.path.isfile(skyline_app_loglock):
                sleep(.1)
                now = time.time()
            else:
                now = log_wait_for + 1

        logger.info('starting %s run' % skyline_app)
        if os.path.isfile(skyline_app_loglock):
            logger.error('error - bin/%s.d log management seems to have failed, continuing' % skyline_app)
            try:
                os_remove(skyline_app_loglock)
                logger.info('log lock file removed')
            except OSError:
                logger.error('error - failed to remove %s, continuing' % skyline_app_loglock)
                pass
        else:
            logger.info('bin/%s.d log management done' % skyline_app)

        try:
            logger.info('starting %s - %s' % (skyline_app, skyline_version))
        except:
            logger.info('starting %s - version UNKNOWN' % (skyline_app))
        logger.info('hosted at %s' % settings.WEBAPP_IP)
        logger.info('running on port %d' % settings.WEBAPP_PORT)

        app.run(settings.WEBAPP_IP, settings.WEBAPP_PORT)


def run():
    """
    Start the Webapp server
    """
    if not isdir(settings.PID_PATH):
        print ('pid directory does not exist at %s' % settings.PID_PATH)
        sys.exit(1)

    if not isdir(settings.LOG_PATH):
        print ('log directory does not exist at %s' % settings.LOG_PATH)
        sys.exit(1)

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter("%(asctime)s :: %(process)s :: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler = logging.handlers.TimedRotatingFileHandler(
        logfile,
        when="midnight",
        interval=1,
        backupCount=5)

    memory_handler = logging.handlers.MemoryHandler(100,
                                                    flushLevel=logging.DEBUG,
                                                    target=handler)
    handler.setFormatter(formatter)
    logger.addHandler(memory_handler)

    # Validate settings variables
    valid_settings = validate_settings_variables(skyline_app)

    if not valid_settings:
        print ('error :: invalid variables in settings.py - cannot start')
        sys.exit(1)

    try:
        settings.WEBAPP_SERVER
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_SERVER'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_SERVER'))
        sys.exit(1)
    try:
        settings.WEBAPP_IP
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_IP'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_IP'))
        sys.exit(1)
    try:
        settings.WEBAPP_PORT
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_PORT'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_PORT'))
        sys.exit(1)
    try:
        settings.WEBAPP_AUTH_ENABLED
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_AUTH_ENABLED'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_AUTH_ENABLED'))
        sys.exit(1)
    try:
        settings.WEBAPP_IP_RESTRICTED
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_IP_RESTRICTED'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_IP_RESTRICTED'))
        sys.exit(1)
    try:
        settings.WEBAPP_AUTH_USER
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_AUTH_USER'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_AUTH_USER'))
        sys.exit(1)
    try:
        settings.WEBAPP_AUTH_USER_PASSWORD
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_AUTH_USER_PASSWORD'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_AUTH_USER_PASSWORD'))
        sys.exit(1)
    try:
        settings.WEBAPP_ALLOWED_IPS
    except:
        logger.error('error :: failed to determine %s from settings.py' % str('WEBAPP_ALLOWED_IPS'))
        print ('Failed to determine %s from settings.py' % str('WEBAPP_ALLOWED_IPS'))
        sys.exit(1)

    webapp = App()

# Does this make it log?
#    if len(sys.argv) > 1 and sys.argv[1] == 'run':
#        webapp.run()
#    else:
#        daemon_runner = runner.DaemonRunner(webapp)
#        daemon_runner.daemon_context.files_preserve = [handler.stream]
#        daemon_runner.do_action()

    daemon_runner = runner.DaemonRunner(webapp)
    daemon_runner.daemon_context.files_preserve = [handler.stream]
    daemon_runner.do_action()


if __name__ == "__main__":
    run()
