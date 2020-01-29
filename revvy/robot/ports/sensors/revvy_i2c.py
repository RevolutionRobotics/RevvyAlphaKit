# SPDX-License-Identifier: GPL-3.0-only
import struct
from enum import Enum

from revvy.robot.ports.common import PortInstance
from revvy.robot.ports.sensors.base import BaseSensorPortDriver

colors = (
    "None",
    "Black",
    "Blue",
    "Green",
    "Yellow",
    "Cyan",
    "Red",
    "White",
    "Purple"
)


class RevvyI2CSensorState(Enum):
    STATE_ERROR = 0
    STATE_RESET = 1
    STATE_DETECTED_BOOTLOADER = 2
    STATE_DETECTED_FIRMWARE = 3
    STATE_OPERATIONAL = 4


class BaseRevvyI2CSensor(BaseSensorPortDriver):
    WRITE_SENSOR_ADDRESS = 0
    WRITE_SELECT_SENSOR_MODE = 1

    SENSOR_MODE_IDLE = 0
    SENSOR_MODE_DEMO = 1

    def __init__(self, sensor_address, port: PortInstance):
        super().__init__("RevvyI2C", port)
        assert 0 <= sensor_address <= 255, f"Invalid I2C sensor address {sensor_address}"
        self._sensor_address = sensor_address

    def on_port_type_set(self):
        self._interface.write_sensor_port(self._port.id, (self.WRITE_SENSOR_ADDRESS, self._sensor_address))

    def update_status(self, data):
        state = RevvyI2CSensorState(data[0])

        if state == RevvyI2CSensorState.STATE_DETECTED_FIRMWARE:
            print(state)
            self._interface.write_sensor_port(self._port.id, (self.WRITE_SELECT_SENSOR_MODE, self.SENSOR_MODE_DEMO))
        elif state == RevvyI2CSensorState.STATE_OPERATIONAL:
            if len(data) > 2:
                state, mode, line_pos, color = struct.unpack('<4b', data)
                print(line_pos, colors[color])
        else:
            print(state)

    def convert_sensor_value(self, raw):
        pass


def revvy_color_sensor(port: PortInstance, _):
    sensor = BaseRevvyI2CSensor(0xC0, port)
    return sensor
