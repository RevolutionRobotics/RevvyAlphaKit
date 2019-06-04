import time

from revvy.functions import hex2rgb
from revvy.ports.motor import MotorPortInstance, MotorPortHandler
from revvy.ports.sensor import SensorPortInstance, SensorPortHandler


class Wrapper:
    def __init__(self, robot, resources: dict, priority=0):
        self._resources = resources
        self._priority = priority
        self._robot = robot

    @property
    def is_stop_requested(self):
        return self._robot.is_stop_requested

    def try_take(self, resource_name):
        return self._resources[resource_name].request(self._priority)

    def using_resource(self, resource_name, callback):
        resource = self.try_take(resource_name)
        if resource:
            try:
                resource.run(callback)
            finally:
                resource.release()


class SensorPortWrapper(Wrapper):
    """Wrapper class to expose sensor ports to user scripts"""
    def __init__(self, robot, sensor: SensorPortInstance, resources: dict, priority=0):
        super().__init__(robot, resources, priority)
        self._sensor = sensor

    def configure(self, config_name):
        self._sensor.configure(config_name)

    def read(self):
        """Return the last converted value"""
        return self._sensor.value


class MotorPortWrapper(Wrapper):
    """Wrapper class to expose motor ports to user scripts"""
    def __init__(self, robot, motor: MotorPortInstance, resources: dict, priority=0):
        super().__init__(robot, resources, priority)
        self._motor = motor

    def configure(self, config_name):
        self._motor.configure(config_name)

    def move(self, direction, amount, unit_amount, limit, unit_limit):
        if unit_amount in [MotorConstants.UNIT_ROT, MotorConstants.UNIT_DEG]:
            if unit_amount == MotorConstants.UNIT_ROT:
                amount = amount * 360

            if direction == MotorConstants.DIR_CCW:
                amount *= -1

            # start moving depending on limits
            resource = self.try_take('motor_{}'.format(self._motor.id))
            if resource:
                try:
                    if unit_limit == MotorConstants.UNIT_SPEED_RPM:
                        resource.run(lambda: self._motor.set_position(amount, speed_limit=limit))
                    elif unit_limit == MotorConstants.UNIT_SPEED_PWR:
                        resource.run(lambda: self._motor.set_position(amount, power_limit=limit))
                    else:
                        raise ValueError

                    # wait for movement to finish
                    while not resource.is_interrupted and self._motor.is_moving:
                        time.sleep(0.2)
                finally:
                    resource.release()

        elif unit_amount == MotorConstants.UNIT_SEC:
            # start moving depending on limits
            resource = self.try_take('motor_{}'.format(self._motor.id))
            if resource:
                try:
                    if unit_limit == MotorConstants.UNIT_SPEED_RPM:
                        resource.run(lambda: self._motor.set_speed(limit))
                    elif unit_limit == MotorConstants.UNIT_SPEED_PWR:
                        resource.run(lambda: self._motor.set_speed(900, power_limit=limit))
                    else:
                        raise ValueError

                    time.sleep(amount)

                    resource.run(lambda: self._motor.set_speed(0))
                finally:
                    resource.release()
        else:
            raise ValueError

    def spin(self, direction, rotation, unit_rotation):
        # start moving depending on limits
        resource = self.try_take('motor_{}'.format(self._motor.id))
        if resource:
            try:
                if unit_rotation == MotorConstants.UNIT_SPEED_RPM:
                    if direction == MotorConstants.DIR_CCW:
                        rotation *= -1

                    resource.run(lambda: self._motor.set_speed(rotation))

                elif unit_rotation == MotorConstants.UNIT_SPEED_PWR:
                    if direction == MotorConstants.DIR_CW:
                        speed = 900
                    else:
                        speed = -900

                    resource.run(lambda: self._motor.set_speed(speed, power_limit=rotation))

                else:
                    raise ValueError
            finally:
                resource.release()

    def stop(self, action):
        if action == MotorConstants.ACTION_STOP_AND_HOLD:
            self.using_resource('motor_{}'.format(self._motor.id), lambda: self._motor.set_speed(0))
        elif action == MotorConstants.ACTION_RELEASE:
            self.using_resource('motor_{}'.format(self._motor.id), lambda: self._motor.set_power(0))
        else:
            raise ValueError


class RingLedWrapper(Wrapper):
    """Wrapper class to expose LED ring to user scripts"""
    def __init__(self, robot, ring_led, resources: dict, priority=0):
        super().__init__(robot, resources, priority)
        self._ring_led = ring_led
        self._user_leds = [0] * ring_led.count

    @property
    def scenario(self):
        return self._ring_led.scenario

    def set_scenario(self, scenario):
        self.using_resource('led_ring', lambda: self._ring_led.set_scenario(scenario))

    def set(self, led_index, str_color):
        if type(led_index) is not list:
            led_index = [led_index]

        color = hex2rgb(str_color)

        for idx in led_index:
            if not (1 <= idx <= self._ring_led.count):
                raise ValueError('Led index invalid: {}'.format(idx))
            self._user_leds[idx - 1] = color

        self.using_resource('led_ring', lambda: self._ring_led.display_user_frame(self._user_leds))


class PortCollection:
    def __init__(self, ports: list, port_map: list, names: dict):
        self._ports = ports
        self._portMap = port_map
        self._portNameMap = names

    def __getitem__(self, item):
        if item is str:
            item = self._portNameMap[item]
        return self._ports[self._portMap[item]]

    def __iter__(self):
        return self._ports.__iter__()


class MotorConstants:
    DIR_CW = 0
    DIR_CCW = 1

    DIRECTION_FWD = 0
    DIRECTION_BACK = 1
    DIRECTION_LEFT = 2
    DIRECTION_RIGHT = 3

    UNIT_ROT = 0
    UNIT_SEC = 1
    UNIT_DEG = 2

    UNIT_SPEED_RPM = 0
    UNIT_SPEED_PWR = 1

    ACTION_STOP_AND_HOLD = 0
    ACTION_RELEASE = 1


class DriveTrainWrapper(Wrapper):
    def __init__(self, robot, drivetrain, resources: dict, priority=0):
        super().__init__(robot, resources, priority)
        self._drivetrain = drivetrain

    def drive(self, direction, rotation, unit_rotation, speed, unit_speed):
        if unit_rotation == MotorConstants.UNIT_ROT:
            degrees = rotation * 360
            if direction in [MotorConstants.DIRECTION_BACK, MotorConstants.DIRECTION_RIGHT]:
                degrees *= -1

            if direction in [MotorConstants.DIRECTION_FWD, MotorConstants.DIRECTION_BACK]:
                print('Moving {} degrees'.format(degrees))
                left_degrees = degrees
                right_degrees = degrees
            else:
                print('Turning {} degrees'.format(degrees))
                left_degrees = -degrees
                right_degrees = degrees

            # start moving depending on limits
            resource = self.try_take('drivetrain')
            if resource:
                try:
                    if unit_speed == MotorConstants.UNIT_SPEED_RPM:
                        resource.run(lambda: self._drivetrain.move(left_degrees, right_degrees, speed, speed))
                    elif unit_speed == MotorConstants.UNIT_SPEED_PWR:
                        resource.run(lambda: self._drivetrain.move(left_degrees, right_degrees, power_limit=speed))
                    else:
                        raise ValueError

                    # wait for movement to finish
                    while not resource.is_interrupted and self._drivetrain.is_moving:
                        time.sleep(0.2)
                finally:
                    resource.release()

        elif unit_rotation == MotorConstants.UNIT_SEC:
            # start moving depending on limits
            resource = self.try_take('drivetrain')
            if resource:
                try:
                    if unit_speed == MotorConstants.UNIT_SPEED_RPM:
                        resource.run(lambda: self._drivetrain.set_speeds(speed, speed))
                    elif unit_speed == MotorConstants.UNIT_SPEED_PWR:
                        resource.run(lambda: self._drivetrain.set_speeds(900, 900, power_limit=speed))
                    else:
                        raise ValueError

                    time.sleep(rotation)

                    resource.run(lambda: self._drivetrain.set_speeds(0, 0))
                finally:
                    resource.release()
        else:
            raise ValueError

    def set_speeds(self, sl, sr):
        self.using_resource('drivetrain', lambda: self._drivetrain.set_speeds(sl, sr))


class RemoteControllerWrapper:
    def __init__(self, remote_controller):
        self._remote_controller = remote_controller

        self.is_button_pressed = remote_controller.is_button_pressed
        self.analog_value = remote_controller.analog_value


# FIXME: type hints missing because of circular reference that causes ImportError
class RobotInterface:
    """Wrapper class that exposes API to user-written scripts"""
    def __init__(self, script, robot, priority=0):
        motor_wrappers = list(MotorPortWrapper(self, port, robot.resources, priority) for port in robot._motor_ports)
        sensor_wrappers = list(SensorPortWrapper(self, port, robot.resources, priority) for port in robot._sensor_ports)
        self._motors = PortCollection(motor_wrappers, MotorPortHandler.motorPortMap, robot.config.motors.names)
        self._sensors = PortCollection(sensor_wrappers, SensorPortHandler.sensorPortMap, robot.config.sensors.names)
        self._ring_led = RingLedWrapper(self, robot._ring_led, robot.resources, priority)
        self._drivetrain = DriveTrainWrapper(self, robot._drivetrain, robot.resources, priority)
        self._remote_controller = RemoteControllerWrapper(robot._remote_controller)

        self._script = script

        # shorthand functions
        self.drive = self._drivetrain.drive

    def stop_all_motors(self, action):
        for motor in self._motors:
            motor.stop(action)

    @property
    def is_stop_requested(self):
        return self._script.is_stop_requested

    @property
    def motors(self):
        return self._motors

    @property
    def sensors(self):
        return self._sensors

    @property
    def led(self):
        return self._ring_led

    @property
    def drivetrain(self):
        return self._drivetrain

    @property
    def controller(self):
        return self._remote_controller

    def play_tune(self, name): pass  # TODO
    def play_note(self): pass  # TODO

    # property alias
    led_ring = led
