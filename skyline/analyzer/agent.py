import logging
import sys
import traceback
from os import getpid
from os.path import isdir
from daemon import runner
from time import sleep, time

from logging.handlers import TimedRotatingFileHandler, MemoryHandler

import os.path
sys.path.append(os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir))
sys.path.insert(0, os.path.dirname(__file__))

if True:
    import settings
    from validate_settings import validate_settings_variables
    from analyzer import Analyzer

skyline_app = 'analyzer'
skyline_app_logger = '%sLog' % skyline_app
logger = logging.getLogger(skyline_app_logger)
logfile = '%s/%s.log' % (settings.LOG_PATH, skyline_app)


class AnalyzerAgent():
    """
    The AnalyzerAgent class does the follow:

    ensures that the required OS resources as defined by the various settings
    are available for the app.
    """

    def __init__(self):
        self.stdin_path = '/dev/null'
        self.stdout_path = '%s/%s.log' % (settings.LOG_PATH, skyline_app)
        self.stderr_path = '%s/%s.log' % (settings.LOG_PATH, skyline_app)
        self.pidfile_path = '%s/%s.pid' % (settings.PID_PATH, skyline_app)
        self.pidfile_timeout = 5

    def run(self):
        logger.info('agent starting skyline %s' % skyline_app)
        Analyzer(getpid()).start()

        while 1:
            sleep(100)


def run():
    """
    Check that all the `ALGORITHMS` can be run.

    Start the AnalyzerAgent.

    Start the logger.
    """
    if not isdir(settings.PID_PATH):
        print('pid directory does not exist at %s' % settings.PID_PATH)
        sys.exit(1)

    if not isdir(settings.LOG_PATH):
        print('log directory does not exist at %s' % settings.LOG_PATH)
        sys.exit(1)

    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s :: %(process)s :: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler = logging.handlers.TimedRotatingFileHandler(
        logfile,
        when="midnight",
        interval=1,
        backupCount=5)

    memory_handler = logging.handlers.MemoryHandler(256,
                                                    flushLevel=logging.DEBUG,
                                                    target=handler)
    handler.setFormatter(formatter)
    logger.addHandler(memory_handler)

    # Validate settings variables
    valid_settings = validate_settings_variables(skyline_app)

    if not valid_settings:
        print ('error :: invalid variables in settings.py - cannot start')
        sys.exit(1)

    # Make sure we can run all the algorithms
    try:
        # from analyzer import algorithms
        import algorithms
        logger.info('Testing algorithms')
        timeseries = map(list, zip(map(float, range(int(time()) - 86400, int(time()) + 1)), [1] * 86401))
        # ensemble = [globals()[algorithm](timeseries) for algorithm in settings.ALGORITHMS]
        ensemble = [getattr(algorithms, algorithm)(timeseries) for algorithm in settings.ALGORITHMS]
    except KeyError as e:
        print ('Algorithm %s deprecated or not defined; check settings.ALGORITHMS' % e)
        sys.exit(1)
    except Exception as e:
        print ('Algorithm test run failed.')
        traceback.print_exc()
        sys.exit(1)

    logger.info('Tested algorithms')
    del timeseries
    del ensemble

    analyzer = AnalyzerAgent()

    logger.info('starting analyzer.run')

    memory_handler.flush

    if len(sys.argv) > 1 and sys.argv[1] == 'run':
        analyzer.run()
    else:
        daemon_runner = runner.DaemonRunner(analyzer)
        daemon_runner.daemon_context.files_preserve = [handler.stream]
        daemon_runner.do_action()

    logger.info('stopping analyzer')
    memory_handler.flush


if __name__ == '__main__':
    run()
