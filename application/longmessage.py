import collections
from json import JSONDecodeError
from file_storage import *


def hexdigest2bytes(hexdigest):
    return b"".join([int(hexdigest[i:i + 2], 16).to_bytes(1, byteorder="big") for i in range(0, len(hexdigest), 2)])


def bytes2hexdigest(bytes):
    return "".join([hex(byte)[2:] for byte in bytes])


LongMessageStatusInfo = collections.namedtuple('LongMessageStatusInfo', ['status', 'md5', 'length'])


class LongMessageStatus:
    UNUSED = 0
    UPLOAD = 1
    VALIDATION = 2
    READY = 3
    VALIDATION_ERROR = 4


class LongMessageType:
    FIRMWARE_DATA = 1
    FRAMEWORK_DATA = 2
    CONFIGURATION_DATA = 3
    TEST_KIT = 4
    MAX = 4


class MessageType:
    SELECT_LONG_MESSAGE_TYPE = 0
    INIT_TRANSFER = 1
    UPLOAD_MESSAGE = 2
    FINALIZE_MESSAGE = 3


class LongMessageError(Exception):
    def __init__(self, message):
        self.message = message


class LongMessageStorage:
    """
    Stores Long messages on disk, under the storage_dir directory.

    Stores 2 files for each long message:
      x.meta: stores md5 and length in json format for the data
      x.data: stores the actual data
    """

    def __init__(self, storage: StorageInterface):
        self._storage = storage

    def read_status(self, long_message_type):
        print("LongMessageStorage:read_status")
        """Return status with triplet of (LongMessageStatus, md5-hexdigest, length). Last two fields might be None)."""
        self._validate_long_message_type(long_message_type)
        try:
            data = self._storage.read_metadata(long_message_type)
            return LongMessageStatusInfo(LongMessageStatus.READY, data['md5'], data['length'])
        except (IOError, JSONDecodeError):
            return LongMessageStatusInfo(LongMessageStatus.UNUSED, None, None)

    def set_long_message(self, long_message_type, data, md5):
        print("LongMessageStorage:set_long_message")
        self._validate_long_message_type(long_message_type)
        self._storage.write(long_message_type, data, md5)

    def get_long_message(self, long_message_type):
        print("LongMessageStorage:get_long_message")
        return self._storage.read(long_message_type)

    @staticmethod
    def _validate_long_message_type(long_message_type):
        if not (0 < long_message_type < LongMessageType.MAX):
            raise LongMessageError("Invalid long message type {}".format(long_message_type))


class LongMessageAggregator:
    """Helper class for building long messages"""

    def __init__(self, md5):
        self.md5 = md5
        self.data = bytearray()
        self.md5calc = hashlib.md5()
        self.md5computed = None

    @property
    def is_empty(self):
        return len(self.data) != 0

    def append_data(self, data):
        self.data += data
        self.md5calc.update(data)

    def finalize(self):
        """Returns true if the uploaded data matches the predefined md5 checksum."""
        self.md5computed = self.md5calc.hexdigest()
        return self.md5computed == self.md5


class LongMessageHandler:
    """Implements the long message writer/status reader protocol"""

    def __init__(self, long_message_storage):
        self._long_message_storage = long_message_storage
        self._long_message_type = None
        self._status = "READ"
        self._aggregator = None
        self._callback = lambda x, y: None

    def on_message_updated(self, callback):
        self._callback = callback

    def read_status(self):
        print("LongMessageHandler:read_status")
        if self._long_message_type is None:
            return LongMessageStatusInfo(LongMessageStatus.UNUSED, None, None)
        if self._status == "READ":
            return self._long_message_storage.read_status(self._long_message_type)
        if self._status == "INVALID":
            return LongMessageStatusInfo(LongMessageStatus.VALIDATION_ERROR, None, None)
        assert self._status == "WRITE"
        return LongMessageStatusInfo(LongMessageStatus.UPLOAD, self._aggregator.md5, len(self._aggregator.data))

    def select_long_message_type(self, long_message_type):
        print("LongMessageHandler:select_long_message_type")
        self._long_message_type = long_message_type
        self._status = "READ"

    def init_transfer(self, md5):
        print("LongMessageHandler:init_transfer")
        self._status = "WRITE"
        self._aggregator = LongMessageAggregator(md5)

    def upload_message(self, data):
        print("LongMessageHandler:upload_message")
        if self._aggregator is None:
            raise LongMessageError("init-transfer needs to be called before upload_message")
        self._aggregator.append_data(data)

    def finalize_message(self):
        print("LongMessageHandler:finalize_message")
        if self._aggregator is None:
            raise LongMessageError("init-transfer needs to be called before finalize_message")

        if not self._aggregator.is_empty:
            self._callback(self._long_message_storage, self._long_message_type)
            self._status = "READ"
        elif self._aggregator.finalize():
            self._long_message_storage.set_long_message(self._long_message_type, self._aggregator.data,
                                                        self._aggregator.md5)
            self._callback(self._long_message_storage, self._long_message_type)
            self._status = "READ"
        else:
            self._status = "INVALID"