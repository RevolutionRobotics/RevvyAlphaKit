# SPDX-License-Identifier: GPL-3.0-only

import hashlib
import struct
from json import JSONDecodeError
from typing import NamedTuple

from revvy.utils.file_storage import StorageInterface, StorageError
from revvy.utils.functions import split
from revvy.utils.logger import get_logger


def hexdigest2bytes(hexdigest):
    """
    >>> hexdigest2bytes("aabbcc")
    b'\\xaa\\xbb\\xcc'
    >>> hexdigest2bytes("ABCDEF")
    b'\\xab\\xcd\\xef'
    >>> hexdigest2bytes("ABCD0F")
    b'\\xab\\xcd\\x0f'
    """
    def hex2byte(h):
        return int(h, 16)
    return bytes(map(hex2byte, split(hexdigest, 2)))


def bytes2hexdigest(hash_bytes):
    """
    >>> bytes2hexdigest(b'\\xaa\\xbb\\xcc')
    'aabbcc'
    >>> bytes2hexdigest(b'\\xAB\\xCD\\xEF')
    'abcdef'
    >>> bytes2hexdigest(b'\\xAB\\xCD\\x0F')
    'abcd0f'
    """
    return "".join('{0:0>2x}'.format(byte) for byte in hash_bytes)


class LongMessageStatus:
    UNUSED = 0
    UPLOAD = 1
    VALIDATION = 2
    READY = 3
    VALIDATION_ERROR = 4


class LongMessageStatusInfo(NamedTuple):
    status: int
    md5: bytes
    length: int


class LongMessageType:
    FIRMWARE_DATA = 1
    FRAMEWORK_DATA = 2
    CONFIGURATION_DATA = 3
    TEST_KIT = 4
    ASSET_DATA = 5
    MAX = 6

    PermanentMessages = [FIRMWARE_DATA, FRAMEWORK_DATA, ASSET_DATA]

    @staticmethod
    def validate(long_message_type):
        if not (0 < long_message_type < LongMessageType.MAX):
            raise LongMessageError(f"Invalid long message type {long_message_type}")


class MessageType:
    SELECT_LONG_MESSAGE_TYPE = 0
    INIT_TRANSFER = 1
    UPLOAD_MESSAGE = 2
    FINALIZE_MESSAGE = 3


class LongMessageError(Exception):
    pass


class LongMessageStorage:
    """Store long messages using the given storage class, with extra validation"""

    def __init__(self, storage: StorageInterface, temp_storage: StorageInterface):
        self._storage = storage
        self._temp_storage = temp_storage
        self._log = get_logger('LongMessageStorage')

    def _get_storage(self, message_type):
        return self._storage if message_type in LongMessageType.PermanentMessages else self._temp_storage

    def read_status(self, long_message_type):
        """Return status with triplet of (LongMessageStatus, md5-hexdigest, length). Last two fields might be None)."""
        self._log("read_status")
        LongMessageType.validate(long_message_type)
        try:
            storage = self._get_storage(long_message_type)
            data = storage.read_metadata(long_message_type)
            return LongMessageStatusInfo(LongMessageStatus.READY, hexdigest2bytes(data['md5']), data['length'])
        except (StorageError, JSONDecodeError):
            return LongMessageStatusInfo(LongMessageStatus.UNUSED, b'', 0)

    def set_long_message(self, long_message_type, data, md5):
        self._log("set_long_message")
        LongMessageType.validate(long_message_type)
        storage = self._get_storage(long_message_type)
        storage.write(long_message_type, data, md5=md5)

    def get_long_message(self, long_message_type):
        self._log("get_long_message")
        storage = self._get_storage(long_message_type)
        return storage.read(long_message_type)


class LongMessageAggregator:
    """Helper class for building long messages"""

    def __init__(self, md5):
        self.md5 = md5
        self.data = bytearray()
        self._md5calc = hashlib.md5()

    def append_data(self, data):
        self.data += data
        self._md5calc.update(data)

    def finalize(self):
        """Returns true if the uploaded data matches the predefined md5 checksum."""
        md5computed = self._md5calc.hexdigest()

        return md5computed == self.md5


class LongMessageHandler:
    """Implements the long message writer/status reader protocol"""

    STATUS_IDLE = 0
    STATUS_READ = 1
    STATUS_WRITE = 2
    STATUS_INVALID = 3

    def __init__(self, long_message_storage):
        self._long_message_storage = long_message_storage
        self._long_message_type = None
        self._status = LongMessageHandler.STATUS_IDLE
        self._aggregator = None
        self._message_updated_callback = None
        self._upload_started_callback = None
        self._upload_finished_callback = None
        self._log = get_logger('LongMessageHandler')

    def on_message_updated(self, callback):
        self._message_updated_callback = callback

    def on_upload_started(self, callback):
        self._upload_started_callback = callback

    def on_upload_finished(self, callback):
        self._upload_finished_callback = callback

    def read_status(self):
        self._log("read_status")

        if self._status == LongMessageHandler.STATUS_IDLE:
            return LongMessageStatusInfo(LongMessageStatus.UNUSED, b'', 0)

        if self._status == LongMessageHandler.STATUS_READ:
            return self._long_message_storage.read_status(self._long_message_type)

        if self._status == LongMessageHandler.STATUS_INVALID:
            return LongMessageStatusInfo(LongMessageStatus.VALIDATION_ERROR, b'', 0)

        assert self._status == LongMessageHandler.STATUS_WRITE
        return LongMessageStatusInfo(LongMessageStatus.UPLOAD, self._aggregator.md5, len(self._aggregator.data))

    def select_long_message_type(self, long_message_type):
        if self._status == LongMessageHandler.STATUS_WRITE:
            upload_finished_callback = self._upload_finished_callback
            if upload_finished_callback:
                upload_finished_callback(long_message_type)

        self._log(f"select_long_message_type: {long_message_type}")
        LongMessageType.validate(long_message_type)
        self._long_message_type = long_message_type
        self._status = LongMessageHandler.STATUS_READ

    def init_transfer(self, md5):
        self._log("init_transfer")

        if self._status == LongMessageHandler.STATUS_WRITE:
            upload_finished_callback = self._upload_finished_callback
            if upload_finished_callback:
                upload_finished_callback(self._long_message_type)

        if self._status == LongMessageHandler.STATUS_IDLE:
            raise LongMessageError("init-transfer needs to be called after select_long_message_type")

        self._status = LongMessageHandler.STATUS_WRITE
        self._aggregator = LongMessageAggregator(md5)

        upload_started_callback = self._upload_started_callback
        if upload_started_callback:
            upload_started_callback(self._long_message_type)

    def upload_message(self, data):
        self._log(f"upload_message ({len(data)} bytes)")

        if self._status != LongMessageHandler.STATUS_WRITE:
            raise LongMessageError("init-transfer needs to be called before upload_message")

        self._aggregator.append_data(data)

    def finalize_message(self):
        self._log("finalize_message")

        if self._status == LongMessageHandler.STATUS_IDLE:
            raise LongMessageError("init-transfer needs to be called before finalize_message")

        updated_callback = self._message_updated_callback
        upload_finished_callback = self._upload_finished_callback

        if self._status == LongMessageHandler.STATUS_READ:
            # shortcut that activates a message which is already on the robot

            # observer must take care of verifying that there is actually a message
            if updated_callback:
                updated_callback(self._long_message_storage, self._long_message_type)

        elif self._status == LongMessageHandler.STATUS_WRITE:
            if upload_finished_callback:
                upload_finished_callback(self._long_message_type)
            if self._aggregator.finalize():
                self._long_message_storage.set_long_message(self._long_message_type, self._aggregator.data,
                                                            self._aggregator.md5)
                if updated_callback:
                    updated_callback(self._long_message_storage, self._long_message_type)
                self._status = LongMessageHandler.STATUS_READ
            else:
                self._status = LongMessageHandler.STATUS_INVALID

        else:
            # INVALID status, finalize does nothing
            pass


class LongMessageProtocol:
    RESULT_SUCCESS = 0
    RESULT_INVALID_ATTRIBUTE_LENGTH = 1
    RESULT_UNLIKELY_ERROR = 2

    def __init__(self, handler: LongMessageHandler):
        self._handler = handler

    def handle_read(self):
        try:
            status = self._handler.read_status()
            if status.status in [LongMessageStatus.READY, LongMessageStatus.UPLOAD]:
                # a byte, a 16byte string and an unsigned long packed into a big endian array
                value = struct.pack('>b16sL', status.status, status.md5, status.length)
            else:
                value = bytes((status.status, ))

            return value
        except (IOError, TypeError, JSONDecodeError) as e:
            raise LongMessageError('Could not read long message') from e

    def handle_write(self, header, data):
        if header == MessageType.SELECT_LONG_MESSAGE_TYPE:
            if len(data) == 1:
                self._handler.select_long_message_type(data[0])
                result = LongMessageProtocol.RESULT_SUCCESS
            else:
                result = LongMessageProtocol.RESULT_INVALID_ATTRIBUTE_LENGTH

        elif header == MessageType.INIT_TRANSFER:
            if len(data) == 16:
                self._handler.init_transfer(bytes2hexdigest(data))
                result = LongMessageProtocol.RESULT_SUCCESS
            else:
                result = LongMessageProtocol.RESULT_INVALID_ATTRIBUTE_LENGTH

        elif header == MessageType.UPLOAD_MESSAGE:
            if len(data) > 0:
                self._handler.upload_message(data)
                result = LongMessageProtocol.RESULT_SUCCESS
            else:
                result = LongMessageProtocol.RESULT_INVALID_ATTRIBUTE_LENGTH

        elif header == MessageType.FINALIZE_MESSAGE:
            if len(data) == 0:
                self._handler.finalize_message()
                result = LongMessageProtocol.RESULT_SUCCESS
            else:
                result = LongMessageProtocol.RESULT_INVALID_ATTRIBUTE_LENGTH

        else:
            result = LongMessageProtocol.RESULT_UNLIKELY_ERROR

        return result
