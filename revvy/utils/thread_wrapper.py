# SPDX-License-Identifier: GPL-3.0-only

import time
import traceback
from threading import Event, Thread, Lock

from revvy.utils.logger import get_logger


def _call_callbacks(cb_list: list):
    while len(cb_list) != 0:
        cb = cb_list.pop()
        cb()


class ThreadWrapper:
    """
    Helper class to enable stopping/restarting threads from the outside
    Threads are not automatically stopped (as it is not possible), but a stop request can be read using the
    context object that is passed to the thread function
    """

    def __init__(self, func, name="WorkerThread"):
        self._log = get_logger('ThreadWrapper [{}]'.format(name))
        self._log('created')
        self._exiting = False
        self._lock = Lock()
        self._func = func
        self._stopped_callbacks = []
        self._stop_requested_callbacks = []
        self._control = Event()
        self._thread_stopped_event = Event()
        self._thread_running_event = Event()
        self._ctx = None
        self._was_started = False
        self._thread = Thread(target=self._thread_func, args=())
        self._thread.start()

    def _wait_for_start(self):
        self._control.wait()

        return not self._exiting

    # noinspection PyBroadException
    def _thread_func(self):
        try:
            while self._wait_for_start():
                try:
                    with self._lock:
                        self._ctx = ThreadContext(self)
                        self._was_started = True
                        self._thread_running_event.set()
                        self._control.clear()
                    self._log('thread started')
                    self._func(self._ctx)
                except InterruptedError:
                    self._log('interrupted')
                except Exception:
                    self._log(traceback.format_exc())
                finally:
                    with self._lock:
                        self._log('stopped')
                        self._thread_running_event.clear()
                        _call_callbacks(self._stopped_callbacks)
                        self._ctx = None
        finally:
            self._thread_stopped_event.set()

    @property
    def stopping(self):
        if self._ctx is None:
            return self._thread_stopped_event.is_set()
        return self._ctx.stop_requested

    @property
    def is_running(self):
        return self._thread_running_event.is_set()

    def start(self):
        assert not self._exiting

        self._log('starting')
        self._thread_stopped_event.clear()
        self._control.set()

        return self._thread_running_event

    def stop(self):
        if self.stopping:
            self._log('stop already called')
        else:
            self._log('stopping')

            if self._control.is_set():
                self._log('startup is in progress, wait for thread to start running')
                self._thread_running_event.wait()

            with self._lock:
                if self._thread_running_event.is_set():
                    self._log('register stopped callback in stop')
                    # register callback that sets event when thread stops
                    self._stopped_callbacks.append(self._thread_stopped_event.set)

                    # request thread to stop
                    self._log('request stop')
                    self._ctx.stop()

                    call_callbacks = True
                else:
                    call_callbacks = False
                    self._thread_stopped_event.set()

            if call_callbacks:
                self._log('call stop requested callbacks')
                _call_callbacks(self._stop_requested_callbacks)
                self._log('stop requested callbacks finished')

        return self._thread_stopped_event

    def exit(self):
        self._log('exiting')

        # stop current run
        evt = self.stop()

        self._exiting = True
        self._control.set()
        self._log('waiting for stop event to be set')
        evt.wait()
        self._log('joining thread')
        self._thread.join()
        self._log('exited')

    def on_stopped(self, callback):
        with self._lock:
            call = self._was_started and not self._ctx
            if not call:
                self._stopped_callbacks.append(callback)

        if call:
            callback()

    def on_stop_requested(self, callback):
        with self._lock:
            call = self._ctx and self._ctx.stop_requested
            if not call:
                self._stop_requested_callbacks.append(callback)
        if call:
            callback()


class ThreadContext:
    def __init__(self, thread: ThreadWrapper):
        self._stop_event = Event()

        self.stop = self._stop_event.set
        self.on_stopped = thread.on_stop_requested

    def sleep(self, s):
        if self._stop_event.wait(s):
            raise InterruptedError

    @property
    def stop_requested(self):
        return self._stop_event.is_set()


def periodic(fn, period, name="PeriodicThread"):
    """
    Call fn periodically

    :param fn: the function to run
    :param period: period time in seconds
    :param name: optional name to prefix the thread log messages
    :return: the created thread object
    """
    def _call_periodically(ctx: ThreadContext):
        _next_call = time.time()
        while not ctx.stop_requested:
            fn()

            _next_call += period
            diff = _next_call - time.time()
            if diff > 0:
                time.sleep(diff)
            else:
                # period was missed, let's restart
                _next_call = time.time()

    return ThreadWrapper(_call_periodically, name)
