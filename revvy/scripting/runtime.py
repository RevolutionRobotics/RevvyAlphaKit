from revvy.scripting.robot_interface import RobotInterface
from revvy.thread_wrapper import *
import time


class TimeWrapper:
    def __init__(self, ctx: ThreadContext):
        self._ctx = ctx

    def time(self):
        return time.time()

    def sleep(self, s):
        self._ctx.sleep(s)


class ScriptHandle:
    def __init__(self, script, name, global_variables: dict):
        self._globals = global_variables

        self._thread = ThreadWrapper(self._run, 'ScriptThread: {}'.format(name))

        if callable(script):
            self._runnable = script
        else:
            self._runnable = lambda x: exec(script, x)

    @property
    def is_stop_requested(self):
        return self._thread.stopping

    def on_stopped(self, callback):
        self._thread.on_stopped(callback)

    def assign(self, name, value):
        self._globals[name] = value

    def _run(self, ctx):
        self._runnable({
            **self._globals,
            'ctx': ctx,
            'time': TimeWrapper(ctx)
        })

    def start(self):
        self._thread.start()

    def stop(self):
        self._thread.stop()

    def cleanup(self):
        self._thread.exit()


class ScriptManager:
    def __init__(self, robot):
        self._robot = robot
        self._globals = {}
        self._scripts = {}

    def reset(self):
        for script in self._scripts:
            self._scripts[script].cleanup()

        self._globals = {}
        self._scripts = {}

    def assign(self, name, value):
        self._globals[name] = value
        for script in self._scripts:
            self._scripts[script].assign(name, value)

    def add_script(self, name, script, priority=0):
        if name in self._scripts:
            self._scripts[name].cleanup()

        print('New script: {}'.format(name))
        script = ScriptHandle(script, name, self._globals)
        script.assign('robot', RobotInterface(script, self._robot, priority))
        self._scripts[name] = script

    def __getitem__(self, name):
        return self._scripts[name]
