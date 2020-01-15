# SPDX-License-Identifier: GPL-3.0-only

from collections import namedtuple

from revvy.mcu.commands import MotorPortControlCommand
from revvy.mcu.rrrc_control import RevvyControl
from revvy.robot.ports.common import PortHandler, PortInstance, PortDriver
import struct

from revvy.utils.awaiter import Awaiter, AwaiterSignal, AwaiterImpl
from revvy.utils.functions import clip
from revvy.utils.logger import get_logger

DcMotorStatus = namedtuple("DcMotorStatus", ['position', 'speed', 'power'])


def create_motor_port_handler(interface: RevvyControl):
    port_amount = interface.get_motor_port_amount()
    port_types = interface.get_motor_port_types()

    drivers = {
        'DcMotor': DcMotorController
    }
    handler = PortHandler("Motor", interface, drivers, NullMotor(), port_amount, port_types)
    handler._set_port_type = interface.set_motor_port_type

    return handler


class NullMotor(PortDriver):
    def __init__(self):
        super().__init__('NotConfigured')

    def on_port_type_set(self):
        pass

    def on_status_changed(self, cb):
        pass

    @property
    def speed(self):
        return 0

    @property
    def position(self):
        return 0

    @property
    def power(self):
        return 0

    @property
    def is_moving(self):
        return False

    def set_speed(self, speed, power_limit=None):
        pass

    def set_position(self, position: int, speed_limit=None, power_limit=None, pos_type='absolute') -> Awaiter:
        return AwaiterImpl.from_state(AwaiterSignal.FINISHED)

    def set_power(self, power):
        pass

    def update_status(self, data):
        pass

    def get_status(self):
        return DcMotorStatus(position=0, speed=0, power=0)


class DcMotorPowerRequest(MotorPortControlCommand):
    def __init__(self, port_idx, power):
        power = clip(power, -100, 100)
        if power < 0:
            power = 256 + power

        super().__init__(port_idx, [0, power])


class DcMotorSpeedRequest(MotorPortControlCommand):
    def __init__(self, port_idx, speed, power_limit=None):
        if power_limit is None:
            control = struct.pack("<bf", 1, speed)
        else:
            control = struct.pack("<bff", 1, speed, power_limit)

        super().__init__(port_idx, control)


class DcMotorPositionRequest(MotorPortControlCommand):
    REQUEST_ABSOLUTE = 2
    REQUEST_RELATIVE = 3

    def __init__(self, port_idx, position, request_type, speed_limit=None, power_limit=None):
        position = int(position)

        if speed_limit is not None and power_limit is not None:
            control = struct.pack("<blff", request_type, position, speed_limit, power_limit)
        elif speed_limit is not None:
            control = struct.pack("<blbf", request_type, position, 1, speed_limit)
        elif power_limit is not None:
            control = struct.pack("<blbf", request_type, position, 0, power_limit)
        else:
            control = struct.pack("<bl", request_type, position)

        super().__init__(port_idx, control)


class DcMotorController(PortDriver):
    """Generic driver for dc motors"""
    def __init__(self, port: PortInstance, port_config):
        super().__init__('DcMotor')
        self._name = 'Motor {}'.format(port.id)
        self._port = port
        self._port_config = port_config
        self._log = get_logger(self._name)

        self._configure = lambda cfg: port.interface.set_motor_port_config(port.id, cfg)
        self._read = lambda: port.interface.get_motor_position(port.id)

        self._pos = 0
        self._speed = 0
        self._power = 0
        self._pos_reached = None

        self._status_changed_callback = None
        self._awaiter = None

        self._timeout = 0

    def on_port_type_set(self):
        (posP, posI, posD, speedLowerLimit, speedUpperLimit) = self._port_config['position_controller']
        (speedP, speedI, speedD, powerLowerLimit, powerUpperLimit) = self._port_config['speed_controller']
        (decMax, accMax) = self._port_config['acceleration_limits']

        config = []
        config += list(struct.pack("<h", self._port_config['encoder_resolution']))
        config += list(struct.pack("<{}".format("f" * 5), posP, posI, posD, speedLowerLimit, speedUpperLimit))
        config += list(struct.pack("<{}".format("f" * 5), speedP, speedI, speedD, powerLowerLimit, powerUpperLimit))
        config += list(struct.pack("<ff", decMax, accMax))

        self._log('Sending configuration: {}'.format(config))

        self._configure(config)

    def on_status_changed(self, cb):
        self._status_changed_callback = cb

    def _raise_status_changed_callback(self):
        if self._status_changed_callback:
            self._status_changed_callback(self._port)

    def _cancel_awaiter(self):
        awaiter, self._awaiter = self._awaiter, None
        if awaiter:
            self._log('Cancelling previous request')
            awaiter.cancel()

    @property
    def speed(self):
        return self._speed

    @property
    def position(self):
        return self._pos

    @property
    def power(self):
        return self._power

    @property
    def is_moving(self):
        if self._pos_reached is not None:
            return self._pos_reached
        else:
            return not (int(self._speed) == 0)

    def set_speed(self, speed, power_limit=None):
        self._cancel_awaiter()
        self._log('set_speed')

        self._port.interface.set_motor_port_control_value(DcMotorSpeedRequest(self._port.id, speed, power_limit))

    def set_position(self, position: int, speed_limit=None, power_limit=None, pos_type='absolute') -> Awaiter:
        """
        @param position: measured in degrees, depending on pos_type
        @param speed_limit: maximum speed in degrees per seconds
        @param power_limit: maximum power in percent
        @param pos_type: 'absolute': turn to this angle, counted from startup; 'relative': turn this many degrees
        """
        self._cancel_awaiter()
        self._log('set_position')

        def _finished():
            self._awaiter = None

        def _canceled():
            self.set_power(0)

        awaiter = AwaiterImpl()
        awaiter.on_result(_finished)
        awaiter.on_cancelled(_canceled)

        self._awaiter = awaiter
        self._pos_reached = False

        req_type = {'absolute': DcMotorPositionRequest.REQUEST_ABSOLUTE,
                    'relative': DcMotorPositionRequest.REQUEST_RELATIVE}[pos_type]
        command = DcMotorPositionRequest(self._port.id, position, req_type, speed_limit, power_limit)
        self._port.interface.set_motor_port_control_value(command)

        return awaiter

    def set_power(self, power):
        self._cancel_awaiter()

        self._log('set_power')
        self._port.interface.set_motor_port_control_value(DcMotorPowerRequest(self._port.id, power))

    def update_status(self, data):
        if len(data) == 9:
            (power, pos, speed) = struct.unpack('<blf', data)
            pos_reached = None
        elif len(data) == 10:
            (power, pos, speed, pos_reached) = struct.unpack('<blfb', data)

            if pos_reached:
                awaiter = self._awaiter
                if awaiter:
                    awaiter.finish()
        else:
            self._log('Received {} bytes of data instead of 9 or 10'.format(len(data)))
            return

        self._pos = pos
        self._speed = speed
        self._power = power
        self._pos_reached = pos_reached

        self._raise_status_changed_callback()

    def get_status(self):
        data = self._read()

        self.update_status(data)
        return DcMotorStatus(position=self._pos, speed=self._speed, power=self._power)
