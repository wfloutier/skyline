import logging
try:
    from Queue import Empty
except:
    from queue import Empty
from time import time, sleep
from threading import Thread
# @modified 20190522 - Task #3034: Reduce multiprocessing Manager list usage
# Use Redis sets in place of Manager().list() to reduce memory and number of
# processes
# from multiprocessing import Process, Manager
from multiprocessing import Process
import os
from os import kill, getpid, listdir
from os.path import join, isfile
from ast import literal_eval

from redis import StrictRedis
from msgpack import Unpacker, packb
import traceback
from sys import version_info
import mysql.connector
from mysql.connector import errorcode

# @added 20190502 - Branch #2646: slack
from sqlalchemy.sql import select

import settings
from skyline_functions import fail_check, mkdir_p

# @added 20170115 - Feature #1854: Ionosphere learn - generations
# Added determination of the learn related variables so that any new metrics
# that Panorama adds to the Skyline database, it adds the default
# IONOSPHERE_LEARN_DEFAULT_ values or the namespace specific values matched
# from settings.IONOSPHERE_LEARN_NAMESPACE_CONFIG to the metric database
# entry.
from ionosphere_functions import get_ionosphere_learn_details

# @added 20190502 - Branch #2646: slack
from database import get_engine, metrics_table_meta, anomalies_table_meta

skyline_app = 'panorama'
skyline_app_logger = '%sLog' % skyline_app
logger = logging.getLogger(skyline_app_logger)
skyline_app_logfile = '%s/%s.log' % (settings.LOG_PATH, skyline_app)
skyline_app_loglock = '%s.lock' % skyline_app_logfile
skyline_app_logwait = '%s.wait' % skyline_app_logfile

python_version = int(version_info[0])

this_host = str(os.uname()[1])

# Converting one settings variable into a local variable, just because it is a
# long string otherwise.
try:
    ENABLE_PANORAMA_DEBUG = settings.ENABLE_PANORAMA_DEBUG
except:
    logger.error('error :: cannot determine ENABLE_PANORAMA_DEBUG from settings')
    ENABLE_PANORAMA_DEBUG = False

try:
    SERVER_METRIC_PATH = '.%s' % settings.SERVER_METRICS_NAME
    if SERVER_METRIC_PATH == '.':
        SERVER_METRIC_PATH = ''
except:
    SERVER_METRIC_PATH = ''

# @added 20190523 - Branch #2646: slack
try:
    SLACK_ENABLED = settings.SLACK_ENABLED
except:
    SLACK_ENABLED = False

skyline_app_graphite_namespace = 'skyline.%s%s' % (skyline_app, SERVER_METRIC_PATH)

failed_checks_dir = '%s_failed' % settings.PANORAMA_CHECK_PATH

# @added 20160907 - Handle Panorama stampede on restart after not running #26
# Allow to expire check if greater than PANORAMA_CHECK_MAX_AGE, backwards
# compatible
try:
    test_max_age_set = 1 + settings.PANORAMA_CHECK_MAX_AGE
    if test_max_age_set > 1:
        max_age = True
    if test_max_age_set == 1:
        max_age = False
    max_age_seconds = settings.PANORAMA_CHECK_MAX_AGE
except:
    max_age = False
    max_age_seconds = 0
expired_checks_dir = '%s_expired' % settings.PANORAMA_CHECK_PATH

# Database configuration
config = {'user': settings.PANORAMA_DBUSER,
          'password': settings.PANORAMA_DBUSERPASS,
          'host': settings.PANORAMA_DBHOST,
          'port': settings.PANORAMA_DBPORT,
          'database': settings.PANORAMA_DATABASE,
          'raise_on_warnings': True}


class Panorama(Thread):
    """
    The Panorama class which controls the panorama thread and spawned processes.
    """

    def __init__(self, parent_pid):
        """
        Initialize Panorama

        Create the :obj:`mysql_conn`

        """
        super(Panorama, self).__init__()
        # @modified 20180519 - Feature #2378: Add redis auth to Skyline and rebrow
        if settings.REDIS_PASSWORD:
            self.redis_conn = StrictRedis(password=settings.REDIS_PASSWORD, unix_socket_path=settings.REDIS_SOCKET_PATH)
        else:
            self.redis_conn = StrictRedis(unix_socket_path=settings.REDIS_SOCKET_PATH)
        self.daemon = True
        self.parent_pid = parent_pid
        self.current_pid = getpid()
        # @modified 20190522 - Task #3034: Reduce multiprocessing Manager list usage
        #                      Task #3032: Debug number of Python processes and memory use
        #                      Branch #3002: docker
        # Reduce amount of Manager instances that are used as each requires a
        # copy of entire memory to be copied into each subprocess so this
        # results in a python process per Manager instance, using as much
        # memory as the parent.  OK on a server, not so much in a container.
        # Disabled all the Manager().list() below and replaced with Redis sets
        # self.anomalous_metrics = Manager().list()
        # self.metric_variables = Manager().list()
        self.mysql_conn = mysql.connector.connect(**config)

    def check_if_parent_is_alive(self):
        """
        Self explanatory
        """
        try:
            kill(self.current_pid, 0)
            kill(self.parent_pid, 0)
        except:
            exit(0)

    """
    These are the panorama mysql functions used to surface and input panorama data
    for timeseries.
    """

    def mysql_select(self, select):
        """
        Select data from mysql database

        :param select: the select string
        :type select: str
        :return: tuple
        :rtype: tuple, boolean

        - **Example usage**::

            query = 'select id, test from test'
            result = self.mysql_select(query)

        - **Example of the 0 indexed results tuple, which can hold multiple results**::

            >> print('results: %s' % str(results))
            results: [(1, u'test1'), (2, u'test2')]

            >> print('results[0]: %s' % str(results[0]))
            results[0]: (1, u'test1')

        .. note::
            - If the MySQL query fails a boolean will be returned not a tuple
                * ``False``
                * ``None``

        """

        try:
            cnx = mysql.connector.connect(**config)
            if ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: connected to mysql')
        except mysql.connector.Error as err:
            logger.error('error :: mysql error - %s' % str(err))
            logger.error('error :: failed to connect to mysql')
            return False

        if cnx:
            try:
                if ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: %s' % (str(select)))
                cursor = cnx.cursor()
                query = ('%s' % (str(select)))
                cursor.execute(query)
                result = cursor.fetchall()
                cursor.close()
                cnx.close()
                return result
            except mysql.connector.Error as err:
                logger.error('error :: mysql error - %s' % str(err))
                logger.error('error :: failed to query database - %s' % (str(select)))
                try:
                    cnx.close()
                    return False
                except:
                    return False
        else:
            if ENABLE_PANORAMA_DEBUG:
                logger.error('error :: failed to connect to mysql')

        # Close the test mysql connection
        try:
            cnx.close()
            return False
        except:
            return False

        return False

    def mysql_insert(self, insert):
        """
        Insert data into mysql table

        :param select: the insert string
        :type select: str
        :return: int
        :rtype: int or boolean

        - **Example usage**::

            query = 'insert into host (host) VALUES (\'this_host\')'
            result = self.mysql_insert(query)

        .. note::
            - If the MySQL query fails a boolean will be returned not a tuple
                * ``False``
                * ``None``

        """

        try:
            cnx = mysql.connector.connect(**config)
            if ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: connected to mysql')
        except mysql.connector.Error as err:
            logger.error('error :: mysql error - %s' % str(err))
            logger.error('error :: failed to connect to mysql')
            raise

        if cnx:
            try:
                cursor = cnx.cursor()
                cursor.execute(insert)
                inserted_id = cursor.lastrowid
                # Make sure data is committed to the database
                cnx.commit()
                cursor.close()
                cnx.close()
                return inserted_id
            except mysql.connector.Error as err:
                logger.error('error :: mysql error - %s' % str(err))
                logger.error('Failed to insert record')
                cnx.close()
                raise
        else:
            cnx.close()
            return False

        return False

    # @added 20170101 - Feature #1830: Ionosphere alerts
    #                   Bug #1460: panorama check file fails
    #                   Panorama check file fails #24
    # Get rid of the skyline_functions imp as imp is deprecated in py3 anyway
    def new_load_metric_vars(self, metric_vars_file):
        """
        Load the metric variables for a check from a metric check variables file

        :param metric_vars_file: the path and filename to the metric variables files
        :type metric_vars_file: str
        :return: the metric_vars module object or ``False``
        :rtype: list

        """
        if os.path.isfile(metric_vars_file):
            logger.info(
                'loading metric variables from metric_check_file - %s' % (
                    str(metric_vars_file)))
        else:
            logger.error(
                'error :: loading metric variables from metric_check_file - file not found - %s' % (
                    str(metric_vars_file)))
            return False

        metric_vars = []
        with open(metric_vars_file) as f:
            for line in f:
                no_new_line = line.replace('\n', '')
                no_equal_line = no_new_line.replace(' = ', ',')
                array = str(no_equal_line.split(',', 1))
                add_line = literal_eval(array)
                metric_vars.append(add_line)

        string_keys = ['metric', 'anomaly_dir', 'added_by', 'app', 'source']
        float_keys = ['value']
        int_keys = ['from_timestamp', 'metric_timestamp', 'added_at', 'full_duration']
        array_keys = ['algorithms', 'triggered_algorithms']
        boolean_keys = ['graphite_metric', 'run_crucible_tests']

        metric_vars_array = []
        for var_array in metric_vars:
            key = None
            value = None
            if var_array[0] in string_keys:
                key = var_array[0]
                value_str = str(var_array[1]).replace("'", '')
                value = str(value_str)
                if var_array[0] == 'metric':
                    metric = value
            if var_array[0] in float_keys:
                key = var_array[0]
                value_str = str(var_array[1]).replace("'", '')
                value = float(value_str)
            if var_array[0] in int_keys:
                key = var_array[0]
                value_str = str(var_array[1]).replace("'", '')
                value = int(value_str)
            if var_array[0] in array_keys:
                key = var_array[0]
                value = literal_eval(str(var_array[1]))
            if var_array[0] in boolean_keys:
                key = var_array[0]
                if str(var_array[1]) == 'True':
                    value = True
                else:
                    value = False
            if key:
                metric_vars_array.append([key, value])

            if len(metric_vars_array) == 0:
                logger.error(
                    'error :: loading metric variables - none found' % (
                        str(metric_vars_file)))
                return False

            if settings.ENABLE_DEBUG:
                logger.info(
                    'debug :: metric_vars determined - metric variable - metric - %s' % str(metric_vars.metric))

        logger.info('debug :: metric_vars for %s' % str(metric))
        logger.info('debug :: %s' % str(metric_vars_array))

        return metric_vars_array

    def update_slack_thread_ts(self, i, base_name, metric_timestamp, slack_thread_ts):
        """
        Update an anomaly record with the slack_thread_ts.

        :param i: python process id
        :param metric_check_file: full path to the metric check file

        :return: returns True

        """

        def get_an_engine():
            try:
                engine, log_msg, trace = get_engine(skyline_app)
                return engine, log_msg, trace
            except:
                logger.error(traceback.format_exc())
                log_msg = 'error :: update_slack_thread_ts :: failed to get MySQL engine in update_slack_thread_ts'
                logger.error('error :: update_slack_thread_ts :: failed to get MySQL engine in update_slack_thread_ts')
                return None, log_msg, trace

        def engine_disposal(engine):
            if engine:
                try:
                    engine.dispose()
                except:
                    logger.error(traceback.format_exc())
                    logger.error('error :: update_slack_thread_ts :: calling engine.dispose()')
            return

        child_process_pid = os.getpid()
        logger.info('update_slack_thread_ts :: child_process_pid %s, processing %s, %s, %s' % (
            str(child_process_pid), base_name, str(metric_timestamp),
            str(slack_thread_ts)))
        try:
            engine, log_msg, trace = get_an_engine()
        except:
            logger.error(traceback.format_exc())
            logger.error('error :: update_slack_thread_ts :: could not get a MySQL engine to update slack_thread_ts in anomalies for %s' % (base_name))
        if not engine:
            logger.error('error :: update_slack_thread_ts :: engine not obtained to update slack_thread_ts in anomalies for %s' % (base_name))
            return False
        try:
            metrics_table, log_msg, trace = metrics_table_meta(skyline_app, engine)
            logger.info(log_msg)
            logger.info('update_slack_thread_ts :: metrics_table OK')
        except:
            logger.error(traceback.format_exc())
            logger.error('error :: update_slack_thread_ts :: failed to get metrics_table meta for %s' % base_name)
        metric_id = None
        try:
            connection = engine.connect()
            stmt = select([metrics_table]).where(metrics_table.c.metric == base_name)
            result = connection.execute(stmt)
            for row in result:
                metric_id = int(row['id'])
            connection.close()
        except:
            logger.error(traceback.format_exc())
            logger.error('error :: update_slack_thread_ts :: could not determine metric id from metrics table')
        logger.info('update_slack_thread_ts :: metric id determined as %s' % str(metric_id))
        if metric_id:
            try:
                anomalies_table, log_msg, trace = anomalies_table_meta(skyline_app, engine)
                logger.info(log_msg)
                logger.info('update_slack_thread_ts :: anomalies_table OK')
            except:
                logger.error(traceback.format_exc())
                logger.error('error :: update_slack_thread_ts :: failed to get anomalies_table meta for %s' % base_name)
        anomaly_id = None
        try:
            connection = engine.connect()
            stmt = select([anomalies_table]).\
                where(anomalies_table.c.metric_id == metric_id).\
                where(anomalies_table.c.anomaly_timestamp == metric_timestamp)
            result = connection.execute(stmt)
            for row in result:
                anomaly_id = int(row['id'])
            connection.close()
        except:
            logger.error(traceback.format_exc())
            logger.error('error :: update_slack_thread_ts :: could not determine anomaly id from anomaly table')
        logger.info('update_slack_thread_ts :: anomaly id determined as %s' % str(anomaly_id))
        anomaly_record_updated = False
        if anomaly_id:
            try:
                connection = engine.connect()
                connection.execute(
                    anomalies_table.update(
                        anomalies_table.c.id == anomaly_id).
                    values(slack_thread_ts=slack_thread_ts))
                connection.close()
                logger.info('update_slack_thread_ts :: updated slack_thread_ts for anomaly id %s' % str(anomaly_id))
                anomaly_record_updated = True
            except:
                logger.error(traceback.format_exc())
                logger.error('error :: update_slack_thread_ts :: could not update slack_thread_ts for anomaly id %s' % str(anomaly_id))
        if engine:
            try:
                engine_disposal(engine)
            except:
                logger.error(traceback.format_exc())
                logger.error('error :: update_slack_thread_ts :: could not dispose engine')
        cache_key = 'panorama.slack_thread_ts.%s.%s' % (str(metric_timestamp), base_name)
        delete_cache_key = False
        if anomaly_record_updated:
            delete_cache_key = True
        if not anomaly_record_updated:
            # Allow for 60 seconds for an anomaly to be added
            now = time()
            anomaly_age = int(now) - int(metric_timestamp)
            if anomaly_age > 60:
                delete_cache_key = True
        if delete_cache_key:
            logger.info('update_slack_thread_ts :: deleting cache_key %s' % cache_key)
            try:
                self.redis_conn.delete(cache_key)
                logger.info('update_slack_thread_ts :: cache_key %s deleted' % cache_key)
            except:
                logger.error(traceback.format_exc())
                logger.error('error :: update_slack_thread_ts :: failed to delete cache_key %s' % cache_key)
        return

    def spin_process(self, i, metric_check_file):
        """
        Assign a metric anomaly to process.

        :param i: python process id
        :param metric_check_file: full path to the metric check file

        :return: returns True

        """

        child_process_pid = os.getpid()
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: child_process_pid - %s' % str(child_process_pid))

        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: processing metric check - %s' % metric_check_file)

        if not os.path.isfile(str(metric_check_file)):
            logger.error('error :: file not found - metric_check_file - %s' % (str(metric_check_file)))
            return

        check_file_name = os.path.basename(str(metric_check_file))
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: check_file_name - %s' % check_file_name)
        check_file_timestamp = check_file_name.split('.', 1)[0]
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: check_file_timestamp - %s' % str(check_file_timestamp))
        check_file_metricname_txt = check_file_name.split('.', 1)[1]
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: check_file_metricname_txt - %s' % check_file_metricname_txt)
        check_file_metricname = check_file_metricname_txt.replace('.txt', '')
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: check_file_metricname - %s' % check_file_metricname)
        check_file_metricname_dir = check_file_metricname.replace('.', '/')
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: check_file_metricname_dir - %s' % check_file_metricname_dir)

        metric_failed_check_dir = '%s/%s/%s' % (failed_checks_dir, check_file_metricname_dir, check_file_timestamp)

        failed_check_file = '%s/%s' % (metric_failed_check_dir, check_file_name)
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: failed_check_file - %s' % failed_check_file)

        # Load and validate metric variables
        try:
            # @modified 20170101 - Feature #1830: Ionosphere alerts
            #                      Bug #1460: panorama check file fails
            #                      Panorama check file fails #24
            # Get rid of the skyline_functions imp as imp is deprecated in py3 anyway
            # Use def new_load_metric_vars(self, metric_vars_file):
            # metric_vars = load_metric_vars(skyline_app, str(metric_check_file))
            metric_vars_array = self.new_load_metric_vars(str(metric_check_file))
        except:
            logger.info(traceback.format_exc())
            logger.error('error :: failed to load metric variables from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        # Test metric variables
        # We use a pythonic methodology to test if the variables are defined,
        # this ensures that if any of the variables are not set for some reason
        # we can handle unexpected data or situations gracefully and try and
        # ensure that the process does not hang.
        metric = None
        try:
            # metric_vars.metric
            # metric = str(metric_vars.metric)
            key = 'metric'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            metric = str(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - metric - %s' % metric)
        except:
            logger.error('error :: failed to read metric variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not metric:
            logger.error('error :: failed to load metric variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        value = None
        # @added 20171214 - Bug #2234: panorama metric_vars value check
        value_valid = None
        try:
            # metric_vars.value
            # value = str(metric_vars.value)
            key = 'value'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            value = float(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - value - %s' % (value))
            # @added 20171214 - Bug #2234: panorama metric_vars value check
            value_valid = True
        except:
            logger.error('error :: failed to read value variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        # @added 20171214 - Bug #2234: panorama metric_vars value check
        # If value was float of 0.0 then this was interpolated as not set
        # if not value:
        if not value_valid:
            # @added 20171214 - Bug #2234: panorama metric_vars value check
            # Added exception handling here
            logger.info(traceback.format_exc())
            logger.error('error :: failed to read value variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        from_timestamp = None
        try:
            # metric_vars.from_timestamp
            # from_timestamp = str(metric_vars.from_timestamp)
            key = 'from_timestamp'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            from_timestamp = int(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - from_timestamp - %s' % from_timestamp)
        except:
            # @added 20160822 - Bug #1460: panorama check file fails
            # Added exception handling here
            logger.info(traceback.format_exc())
            logger.error('error :: failed to read from_timestamp variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not from_timestamp:
            logger.error('error :: failed to load from_timestamp variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        metric_timestamp = None
        try:
            # metric_vars.metric_timestamp
            # metric_timestamp = str(metric_vars.metric_timestamp)
            key = 'metric_timestamp'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            metric_timestamp = int(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - metric_timestamp - %s' % metric_timestamp)
        except:
            logger.error('error :: failed to read metric_timestamp variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not metric_timestamp:
            logger.error('error :: failed to load metric_timestamp variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        algorithms = None
        try:
            # metric_vars.algorithms
            # algorithms = metric_vars.algorithms
            key = 'algorithms'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            algorithms = value_list[0]
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - algorithms - %s' % str(algorithms))
        except:
            logger.error('error :: failed to read algorithms variable from check file setting to all - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not algorithms:
            logger.error('error :: failed to load algorithms variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        triggered_algorithms = None
        try:
            # metric_vars.triggered_algorithms
            # triggered_algorithms = metric_vars.triggered_algorithms
            key = 'triggered_algorithms'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            triggered_algorithms = value_list[0]
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - triggered_algorithms - %s' % str(triggered_algorithms))
        except:
            logger.error('error :: failed to read triggered_algorithms variable from check file setting to all - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not triggered_algorithms:
            logger.error('error :: failed to load triggered_algorithms variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        app = None
        try:
            # metric_vars.app
            # app = str(metric_vars.app)
            key = 'app'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            app = str(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - app - %s' % app)
        except:
            logger.error('error :: failed to read app variable from check file setting to all  - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not app:
            logger.error('error :: failed to load app variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        source = None
        try:
            # metric_vars.source
            # source = str(metric_vars.source)
            key = 'source'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            source = str(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - source - %s' % source)
        except:
            logger.error('error :: failed to read source variable from check file setting to all  - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not app:
            logger.error('error :: failed to load app variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        added_by = None
        try:
            # metric_vars.added_by
            # added_by = str(metric_vars.added_by)
            key = 'added_by'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            added_by = str(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - added_by - %s' % added_by)
        except:
            logger.error('error :: failed to read added_by variable from check file setting to all - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not added_by:
            logger.error('error :: failed to load added_by variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        added_at = None
        try:
            # metric_vars.added_at
            # added_at = str(metric_vars.added_at)
            key = 'added_at'
            value_list = [var_array[1] for var_array in metric_vars_array if var_array[0] == key]
            added_at = str(value_list[0])
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: metric variable - added_at - %s' % added_at)
        except:
            logger.error('error :: failed to read added_at variable from check file setting to all - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        if not added_at:
            logger.error('error :: failed to load added_at variable from check file - %s' % (metric_check_file))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return

        record_anomaly = True
        cache_key = '%s.last_check.%s.%s' % (skyline_app, app, metric)
        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: cache_key - %s.last_check.%s.%s' % (
                skyline_app, app, metric))
        try:
            last_check = self.redis_conn.get(cache_key)
        except Exception as e:
            logger.error(
                'error :: could not query cache_key - %s.last_check.%s.%s - %s' % (
                    skyline_app, app, metric, e))
            last_check = None

        if last_check:
            record_anomaly = False
            logger.info(
                'Panorama metric key not expired - %s.last_check.%s.%s' % (
                    skyline_app, app, metric))

        # @added 20160907 - Handle Panorama stampede on restart after not running #26
        # Allow to expire check if greater than PANORAMA_CHECK_MAX_AGE
        if max_age:
            now = time()
            anomaly_age = int(now) - int(metric_timestamp)
            if anomaly_age > max_age_seconds:
                record_anomaly = False
                logger.info(
                    'Panorama check max age exceeded - %s - %s seconds old, older than %s seconds discarding' % (
                        metric, str(anomaly_age), str(max_age_seconds)))

        if not record_anomaly:
            logger.info('not recording anomaly for - %s' % (metric))
            if os.path.isfile(str(metric_check_file)):
                try:
                    os.remove(str(metric_check_file))
                    logger.info('metric_check_file removed - %s' % str(metric_check_file))
                except OSError:
                    pass

            return

        # Determine id of something thing
        def determine_id(table, key, value):
            """
            Get the id of something from Redis or the database and create a new
            Redis key with the value if one does not exist.

            :param table: table name
            :param key: key name
            :param value: value name
            :type table: str
            :type key: str
            :type value: str
            :return: int or boolean

            """

            query_cache_key = '%s.mysql_ids.%s.%s.%s' % (skyline_app, table, key, value)
            determined_id = None
            redis_determined_id = None
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: query_cache_key - %s' % (query_cache_key))

            try:
                redis_known_id = self.redis_conn.get(query_cache_key)
            except:
                redis_known_id = None

            if redis_known_id:
                unpacker = Unpacker(use_list=False)
                unpacker.feed(redis_known_id)
                redis_determined_id = list(unpacker)

            if redis_determined_id:
                determined_id = int(redis_determined_id[0])

            if determined_id:
                if determined_id > 0:
                    return determined_id

            # Query MySQL
            # @modified 20170913 - Task #2160: Test skyline with bandit
            # Added nosec to exclude from bandit tests
            query = 'select id FROM %s WHERE %s=\'%s\'' % (table, key, value)  # nosec

            # @modified 20170916 - Bug #2166: panorama incorrect mysql_id cache keys
            # Wrap in except
            # results = self.mysql_select(query)
            results = None
            try:
                results = self.mysql_select(query)
            except:
                logger.error('error :: failed to determine results from - %s' % (query))

            determined_id = 0
            if results:
                try:
                    determined_id = int(results[0][0])
                except Exception as e:
                    logger.error(traceback.format_exc())
                    logger.error('error :: determined_id is not an int')
                    determined_id = 0

            if determined_id > 0:
                # Set the key for a week
                if not redis_determined_id:
                    try:
                        self.redis_conn.setex(query_cache_key, 604800, packb(determined_id))
                        logger.info('set redis query_cache_key - %s - id: %s' % (
                            query_cache_key, str(determined_id)))
                    except Exception as e:
                        logger.error(traceback.format_exc())
                        logger.error('error :: failed to set query_cache_key - %s - id: %s' % (
                            query_cache_key, str(determined_id)))
                return int(determined_id)

            # @added 20170115 - Feature #1854: Ionosphere learn - generations
            # Added determination of the learn related variables
            # learn_full_duration_days, learn_valid_ts_older_than,
            # max_generations and max_percent_diff_from_origin value to the
            # insert statement if the table is the metrics table.
            if table == 'metrics' and key == 'metric':
                # Set defaults
                learn_full_duration_days = int(settings.IONOSPHERE_LEARN_DEFAULT_FULL_DURATION_DAYS)
                valid_learning_duration = int(settings.IONOSPHERE_LEARN_DEFAULT_VALID_TIMESERIES_OLDER_THAN_SECONDS)
                max_generations = int(settings.IONOSPHERE_LEARN_DEFAULT_MAX_GENERATIONS)
                max_percent_diff_from_origin = float(settings.IONOSPHERE_LEARN_DEFAULT_MAX_PERCENT_DIFF_FROM_ORIGIN)
                try:
                    use_full_duration, valid_learning_duration, use_full_duration_days, max_generations, max_percent_diff_from_origin = get_ionosphere_learn_details(skyline_app, value)
                    learn_full_duration_days = use_full_duration_days
                except:
                    logger.error(traceback.format_exc())
                    logger.error('error :: failed to get_ionosphere_learn_details for %s' % value)

                logger.info('metric learn details determined for %s' % value)
                logger.info('learn_full_duration_days     :: %s days' % (str(learn_full_duration_days)))
                logger.info('valid_learning_duration      :: %s seconds' % (str(valid_learning_duration)))
                logger.info('max_generations              :: %s' % (str(max_generations)))
                logger.info('max_percent_diff_from_origin :: %s' % (str(max_percent_diff_from_origin)))

            # INSERT because no known id
            # @modified 20170115 - Feature #1854: Ionosphere learn - generations
            # Added the learn_full_duration_days, learn_valid_ts_older_than,
            # max_generations and max_percent_diff_from_origin value to the
            # insert statement if the table is the metrics table.
            # insert_query = 'insert into %s (%s) VALUES (\'%s\')' % (table, key, value)
            if table == 'metrics' and key == 'metric':
                # @modified 20170913 - Task #2160: Test skyline with bandit
                # Added nosec to exclude from bandit tests
                insert_query_string = '%s (%s, learn_full_duration_days, learn_valid_ts_older_than, max_generations, max_percent_diff_from_origin) VALUES (\'%s\', %s, %s, %s, %s)' % (
                    table, key, value, str(learn_full_duration_days),
                    str(valid_learning_duration), str(max_generations),
                    str(max_percent_diff_from_origin))
                insert_query = 'insert into %s' % insert_query_string  # nosec
            else:
                insert_query = 'insert into %s (%s) VALUES (\'%s\')' % (table, key, value)  # nosec

            logger.info('inserting %s into %s table' % (value, table))
            try:
                results = self.mysql_insert(insert_query)
            except:
                logger.error(traceback.format_exc())
                logger.error('error :: failed to determine the id of %s from the insert' % (value))
                raise

            determined_id = 0
            if results:
                determined_id = int(results)
            else:
                logger.error('error :: results not set')
                raise

            if determined_id > 0:
                # Set the key for a week
                if not redis_determined_id:
                    try:
                        self.redis_conn.setex(query_cache_key, 604800, packb(determined_id))
                        logger.info('set redis query_cache_key - %s - id: %s' % (
                            query_cache_key, str(determined_id)))
                    except Exception as e:
                        logger.error(traceback.format_exc())
                        logger.error('%s' % str(e))
                        logger.error('error :: failed to set query_cache_key - %s - id: %s' % (
                            query_cache_key, str(determined_id)))
                return determined_id

            logger.error('error :: failed to determine the inserted id for %s' % value)
            return False

        try:
            added_by_host_id = determine_id('hosts', 'host', added_by)
        except:
            logger.error('error :: failed to determine id of %s' % (added_by))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        try:
            app_id = determine_id('apps', 'app', app)
        except:
            logger.error('error :: failed to determine id of %s' % (app))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        try:
            source_id = determine_id('sources', 'source', source)
        except:
            logger.error('error :: failed to determine id of %s' % (source))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        try:
            metric_id = determine_id('metrics', 'metric', metric)
        except:
            logger.error('error :: failed to determine id of %s' % (metric))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        algorithms_ids_csv = ''
        for algorithm in algorithms:
            try:
                algorithm_id = determine_id('algorithms', 'algorithm', algorithm)
            except:
                logger.error('error :: failed to determine id of %s' % (algorithm))
                fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
                return False
            if algorithms_ids_csv == '':
                algorithms_ids_csv = str(algorithm_id)
            else:
                new_algorithms_ids_csv = '%s,%s' % (algorithms_ids_csv, str(algorithm_id))
                algorithms_ids_csv = new_algorithms_ids_csv

        triggered_algorithms_ids_csv = ''
        for triggered_algorithm in triggered_algorithms:
            try:
                triggered_algorithm_id = determine_id('algorithms', 'algorithm', triggered_algorithm)
            except:
                logger.error('error :: failed to determine id of %s' % (triggered_algorithm))
                fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
                return False
            if triggered_algorithms_ids_csv == '':
                triggered_algorithms_ids_csv = str(triggered_algorithm_id)
            else:
                new_triggered_algorithms_ids_csv = '%s,%s' % (
                    triggered_algorithms_ids_csv, str(triggered_algorithm_id))
                triggered_algorithms_ids_csv = new_triggered_algorithms_ids_csv

        logger.info('inserting anomaly')
        try:
            full_duration = int(metric_timestamp) - int(from_timestamp)
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: full_duration - %s' % str(full_duration))
        except:
            logger.error('error :: failed to determine full_duration')
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        try:
            anomalous_datapoint = round(float(value), 6)
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: anomalous_datapoint - %s' % str(anomalous_datapoint))
        except:
            logger.error('error :: failed to determine anomalous_datapoint')
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        try:
            columns = '%s, %s, %s, %s, %s, %s, %s, %s, %s' % (
                'metric_id', 'host_id', 'app_id', 'source_id',
                'anomaly_timestamp', 'anomalous_datapoint', 'full_duration',
                'algorithms_run', 'triggered_algorithms')
            if settings.ENABLE_PANORAMA_DEBUG:
                logger.info('debug :: columns - %s' % str(columns))
        except:
            logger.error('error :: failed to construct columns string')
            logger.info(traceback.format_exc())
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        try:
            # @modified 20170913 - Task #2160: Test skyline with bandit
            # Added nosec to exclude from bandit tests
            query_string = '(%s) VALUES (%d, %d, %d, %d, %s, %.6f, %d, \'%s\', \'%s\')' % (
                columns, metric_id, added_by_host_id, app_id, source_id,
                metric_timestamp, anomalous_datapoint, full_duration,
                algorithms_ids_csv, triggered_algorithms_ids_csv)
            query = 'insert into anomalies %s' % query_string  # nosec
        except:
            logger.error('error :: failed to construct insert query')
            logger.info(traceback.format_exc())
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        if settings.ENABLE_PANORAMA_DEBUG:
            logger.info('debug :: anomaly insert - %s' % str(query))

        try:
            anomaly_id = self.mysql_insert(query)
            logger.info('anomaly id - %d - created for %s at %s' % (
                anomaly_id, metric, metric_timestamp))
        except:
            logger.error('error :: failed to insert anomaly %s at %s' % (
                anomaly_id, metric, metric_timestamp))
            fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))
            return False

        # Set anomaly record cache key
        try:
            self.redis_conn.setex(
                cache_key, settings.PANORAMA_EXPIRY_TIME, packb(value))
            logger.info('set cache_key - %s.last_check.%s.%s - %s' % (
                skyline_app, app, metric, str(settings.PANORAMA_EXPIRY_TIME)))
        except Exception as e:
            logger.error(
                'error :: could not query cache_key - %s.last_check.%s.%s - %s' % (
                    skyline_app, app, metric, e))

        if os.path.isfile(str(metric_check_file)):
            try:
                os.remove(str(metric_check_file))
                logger.info('metric_check_file removed - %s' % str(metric_check_file))
            except OSError:
                pass

        return anomaly_id

    def run(self):
        """
        Called when the process intializes.

        Determine if what is known in the Skyline DB
        blah

        """

        # Log management to prevent overwriting
        # Allow the bin/<skyline_app>.d to manage the log
        if os.path.isfile(skyline_app_logwait):
            try:
                logger.info('removing %s' % skyline_app_logwait)
                os.remove(skyline_app_logwait)
            except OSError:
                logger.error('error :: failed to remove %s, continuing' % skyline_app_logwait)
                pass

        now = time()
        log_wait_for = now + 5
        while now < log_wait_for:
            if os.path.isfile(skyline_app_loglock):
                sleep(.1)
                now = time()
            else:
                now = log_wait_for + 1

        logger.info('starting %s run' % skyline_app)
        if os.path.isfile(skyline_app_loglock):
            logger.error('error :: bin/%s.d log management seems to have failed, continuing' % skyline_app)
            try:
                os.remove(skyline_app_loglock)
                logger.info('log lock file removed')
            except OSError:
                logger.error('error :: failed to remove %s, continuing' % skyline_app_loglock)
                pass
        else:
            logger.info('bin/%s.d log management done' % skyline_app)

        # See if I am known in the DB, if so, what are my variables
        # self.populate mysql
        # What is my host id in the Skyline panorama DB?
        #   - if not known - INSERT hostname INTO hosts
        # What are the known apps?
        #   - if returned make a dictionary
        # What are the known algorithms?
        #   - if returned make a dictionary

        while 1:
            now = time()

            # Make sure Redis is up
            try:
                self.redis_conn.ping()
                if ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: connected to Redis')
            except:
                logger.error('error :: cannot connect to redis at socket path %s' % (
                    settings.REDIS_SOCKET_PATH))
                sleep(30)
                # @modified 20180519 - Feature #2378: Add redis auth to Skyline and rebrow
                if settings.REDIS_PASSWORD:
                    self.redis_conn = StrictRedis(password=settings.REDIS_PASSWORD, unix_socket_path=settings.REDIS_SOCKET_PATH)
                else:
                    self.redis_conn = StrictRedis(unix_socket_path=settings.REDIS_SOCKET_PATH)
                continue

            # Report app up
            try:
                self.redis_conn.setex(skyline_app, 120, now)
                logger.info('updated Redis key for %s up' % skyline_app)
            except:
                logger.error('error :: failed to update Redis key for %s up' % skyline_app)

            if ENABLE_PANORAMA_DEBUG:
                # Make sure mysql is available
                mysql_down = True
                while mysql_down:

                    query = 'SHOW TABLES'
                    results = self.mysql_select(query)

                    if results:
                        mysql_down = False
                        logger.info('debug :: tested database query - OK')
                    else:
                        logger.error('error :: failed to query database')
                        sleep(30)

            if ENABLE_PANORAMA_DEBUG:
                try:
                    query = 'SELECT id, test FROM test'
                    result = self.mysql_select(query)
                    logger.info('debug :: tested mysql SELECT query - OK')
                    logger.info('debug :: result: %s' % str(result))
                    logger.info('debug :: result[0]: %s' % str(result[0]))
                    logger.info('debug :: result[1]: %s' % str(result[1]))
# Works
# 2016-06-10 19:07:23 :: 4707 :: result: [(1, u'test1')]
                except:
                    logger.error(
                        'error :: mysql error - %s' %
                        traceback.print_exc())
                    logger.error('error :: failed to SELECT')

            # self.populate the database metatdata tables
            # What is my host id in the Skyline panorama DB?
            host_id = False
            # @modified 20170913 - Task #2160: Test skyline with bandit
            # Added nosec to exclude from bandit tests
            query = 'select id FROM hosts WHERE host=\'%s\'' % this_host  # nosec
            results = self.mysql_select(query)
            if results:
                host_id = results[0][0]
                logger.info('host_id: %s' % str(host_id))
            else:
                logger.info('failed to determine host id of %s' % this_host)

            #   - if not known - INSERT hostname INTO host
            if not host_id:
                logger.info('inserting %s into hosts table' % this_host)
                # @modified 20170913 - Task #2160: Test skyline with bandit
                # Added nosec to exclude from bandit tests
                query = 'insert into hosts (host) VALUES (\'%s\')' % this_host  # nosec
                host_id = self.mysql_insert(query)
                if host_id:
                    logger.info('new host_id: %s' % str(host_id))

            if not host_id:
                logger.error(
                    'error :: failed to determine populate %s into the hosts table' %
                    this_host)
                sleep(30)
                continue

            # Like loop through the panorama dir and see if anyone has left you
            # any work, etc
            # Make sure check_dir exists and has not been removed
            try:
                if settings.ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: checking check dir exists - %s' % settings.PANORAMA_CHECK_PATH)
                os.path.exists(settings.PANORAMA_CHECK_PATH)
            except:
                logger.error('error :: check dir did not exist - %s' % settings.PANORAMA_CHECK_PATH)
                mkdir_p(settings.PANORAMA_CHECK_PATH)

                logger.info('check dir created - %s' % settings.PANORAMA_CHECK_PATH)
                os.path.exists(settings.PANORAMA_CHECK_PATH)
                # continue

            """
            Determine if any metric has been added to add
            """
            while True:
                metric_var_files = False
                try:
                    metric_var_files = [f for f in listdir(settings.PANORAMA_CHECK_PATH) if isfile(join(settings.PANORAMA_CHECK_PATH, f))]
                except:
                    logger.error('error :: failed to list files in check dir')
                    logger.info(traceback.format_exc())

                if not metric_var_files:
                    logger.info('sleeping 20 no metric check files')
                    sleep(20)

                # Discover metric anomalies to insert
                metric_var_files = False
                try:
                    metric_var_files = [f for f in listdir(settings.PANORAMA_CHECK_PATH) if isfile(join(settings.PANORAMA_CHECK_PATH, f))]
                except:
                    logger.error('error :: failed to list files in check dir')
                    logger.info(traceback.format_exc())

                if metric_var_files:
                    break

                # @added 20190501 - Branch #2646: slack
                # Check if any Redis keys exist with a slack_thread_ts to update
                # any anomaly records
                slack_thread_ts_updates = None
                # @added 20190523 - Branch #3002: docker
                #                   Branch #2646: slack
                # Only check if slack is enabled
                if SLACK_ENABLED:
                    try:
                        slack_thread_ts_updates = list(self.redis_conn.scan_iter(match='panorama.slack_thread_ts.*'))
                    except:
                        logger.error(traceback.format_exc())
                        logger.error('error :: failed to scan panorama.slack_thread_ts.* from Redis')
                        slack_thread_ts_updates = []

                    if not slack_thread_ts_updates:
                        logger.info('no panorama.slack_thread_ts Redis keys to process, OK')

                if slack_thread_ts_updates:
                    for cache_key in slack_thread_ts_updates:
                        base_name = None
                        metric_timestamp = None
                        try:
                            update_on = self.redis_conn.get(cache_key)
                            # cache_key_value = [base_name, metric_timestamp, slack_thread_ts]
                            update_for = literal_eval(update_on)
                            base_name = str(update_for[0])
                            metric_timestamp = int(float(update_for[1]))
                            slack_thread_ts = float(update_for[2])
                        except:
                            logger.error(traceback.format_exc())
                            logger.error('error :: failed to get details from cache_key %s' % cache_key)
                        update_db_record = False
                        if base_name and metric_timestamp:
                            update_db_record = True
                        else:
                            logger.info('Could not determine base_name and metric_timestamp from cache_key %s, deleting' % cache_key)
                            try:
                                self.redis_conn.delete(cache_key)
                            except:
                                logger.error(traceback.format_exc())
                                logger.error('error :: failed to delete cache_key %s' % cache_key)
                        if update_db_record:
                            # Spawn update_slack_thread_ts process
                            pids = []
                            spawned_pids = []
                            pid_count = 0
                            now = time()
                            for i in range(1, 2):
                                try:
                                    p = Process(target=self.update_slack_thread_ts, args=(i, base_name, metric_timestamp, slack_thread_ts))
                                    pids.append(p)
                                    pid_count += 1
                                    logger.info('starting update_slack_thread_ts')
                                    p.start()
                                    spawned_pids.append(p.pid)
                                except:
                                    logger.info(traceback.format_exc())
                                    logger.error('error :: to start update_slack_thread_ts')
                                    continue
                            p_starts = time()
                            # @modified 20190509 - Branch #2646: slack
                            # If the Skyline MySQL database is on a remote host
                            # 2 seconds here is sometimes not sufficient so
                            # increased to 10
                            while time() - p_starts <= 10:
                                if any(p.is_alive() for p in pids):
                                    # Just to avoid hogging the CPU
                                    sleep(.1)
                                else:
                                    # All the processes are done, break now.
                                    time_to_run = time() - p_starts
                                    logger.info(
                                        '%s :: update_slack_thread_ts completed in %.2f seconds' % (
                                            skyline_app, time_to_run))
                                    break
                            else:
                                # We only enter this if we didn't 'break' above.
                                logger.info('%s :: timed out, killing all update_slack_thread_ts processes' % (skyline_app))
                                for p in pids:
                                    p.terminate()

            metric_var_files_sorted = sorted(metric_var_files)
            metric_check_file = '%s/%s' % (settings.PANORAMA_CHECK_PATH, str(metric_var_files_sorted[0]))

            logger.info('assigning anomaly for insertion - %s' % str(metric_var_files_sorted[0]))

            # Spawn processes
            pids = []
            spawned_pids = []
            pid_count = 0
            now = time()
            for i in range(1, settings.PANORAMA_PROCESSES + 1):
                try:
                    p = Process(target=self.spin_process, args=(i, metric_check_file))
                    pids.append(p)
                    pid_count += 1
                    logger.info('starting %s of %s spin_process/es' % (str(pid_count), str(settings.PANORAMA_PROCESSES)))
                    p.start()
                    spawned_pids.append(p.pid)
                except:
                    logger.error('error :: to start spin_process')
                    logger.info(traceback.format_exc())
                    continue

            # Send wait signal to zombie processes
            # for p in pids:
            #     p.join()
            # Self monitor processes and terminate if any spin_process has run
            # for longer than CRUCIBLE_TESTS_TIMEOUT
            p_starts = time()
            while time() - p_starts <= 20:
                if any(p.is_alive() for p in pids):
                    # Just to avoid hogging the CPU
                    sleep(.1)
                else:
                    # All the processes are done, break now.
                    time_to_run = time() - p_starts
                    logger.info(
                        '%s :: %s spin_process/es completed in %.2f seconds' % (
                            skyline_app, str(settings.PANORAMA_PROCESSES),
                            time_to_run))
                    break
            else:
                # We only enter this if we didn't 'break' above.
                logger.info('%s :: timed out, killing all spin_process processes' % (skyline_app))
                for p in pids:
                    p.terminate()
                    # p.join()

                check_file_name = os.path.basename(str(metric_check_file))
                if settings.ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: check_file_name - %s' % check_file_name)
                check_file_timestamp = check_file_name.split('.', 1)[0]
                if settings.ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: check_file_timestamp - %s' % str(check_file_timestamp))
                check_file_metricname_txt = check_file_name.split('.', 1)[1]
                if settings.ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: check_file_metricname_txt - %s' % check_file_metricname_txt)
                check_file_metricname = check_file_metricname_txt.replace('.txt', '')
                if settings.ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: check_file_metricname - %s' % check_file_metricname)
                check_file_metricname_dir = check_file_metricname.replace('.', '/')
                if settings.ENABLE_PANORAMA_DEBUG:
                    logger.info('debug :: check_file_metricname_dir - %s' % check_file_metricname_dir)

                metric_failed_check_dir = '%s/%s/%s' % (failed_checks_dir, check_file_metricname_dir, check_file_timestamp)

                fail_check(skyline_app, metric_failed_check_dir, str(metric_check_file))

            for p in pids:
                if p.is_alive():
                    logger.info('%s :: stopping spin_process - %s' % (skyline_app, str(p.is_alive())))
                    p.join()
