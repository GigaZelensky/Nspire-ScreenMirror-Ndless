"""libusb bulk I/O for the shared Nspire packet protocol; no TI/libnspire install."""

from nspire_usb import NspireProtocol, WindowsUSB


class LibusbIO:
    def __init__(self, handle, interface, incoming, outgoing, packet_size, module):
        self.handle, self.interface = handle, interface
        self.input, self.output, self.packet_size = incoming, outgoing, packet_size
        self.module = module

    def read(self, maximum, timeout=1500):
        try:
            data = bytes(self.handle.bulkRead(self.input, maximum, timeout=max(1, int(timeout))))
        except self.module.USBErrorTimeout as error:
            raise TimeoutError("Calculator USB transfer timed out") from error
        if not data:
            raise OSError("Empty calculator USB transfer")
        return data

    def write(self, data):
        try:
            written = self.handle.bulkWrite(self.output, data, timeout=1500)
            if written != len(data):
                raise OSError("Incomplete calculator USB write")
            # Match WinUSB SHORT_PACKET_TERMINATE on exact endpoint-sized writes.
            if data and len(data) % self.packet_size == 0:
                self.handle.bulkWrite(self.output, b"", timeout=1500)
        except self.module.USBErrorTimeout as error:
            raise TimeoutError("Calculator USB transfer timed out") from error

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            try:
                handle.releaseInterface(self.interface)
            finally:
                handle.close()


def open_device(device, module):
    handle = device.open()
    try:
        configuration = handle.getConfiguration()
        settings = (
            setting
            for config in device.iterConfigurations()
            if config.getConfigurationValue() == configuration
            for interface in config.iterInterfaces()
            for setting in interface.iterSettings()
        )
        for setting in settings:
            if setting.getAlternateSetting() != 0:
                continue
            endpoints = [e for e in setting.iterEndpoints() if e.getAttributes() & 3 == 2]
            incoming = [e for e in endpoints if e.getAddress() & 0x80]
            outgoing = [e for e in endpoints if not e.getAddress() & 0x80]
            if len(incoming) != 1 or len(outgoing) != 1:
                continue
            number = setting.getNumber()
            handle.setAutoDetachKernelDriver(True)
            handle.claimInterface(number)
            return LibusbIO(
                handle,
                number,
                incoming[0].getAddress(),
                outgoing[0].getAddress(),
                outgoing[0].getMaxPacketSize(),
                module,
            )
        raise OSError("Calculator has no usable USB bulk interface")
    except BaseException:
        handle.close()
        raise


class LinuxUSB(WindowsUSB):
    def __init__(self, path=None, module=None):
        if path:
            raise ValueError(
                "The direct Linux backend does not use --library; use --backend libnspire"
            )
        if module is None:
            import usb1 as module
        self.module = module
        self.context = module.USBContext()
        self.protocol = None

    def begin(self):
        if self.protocol:
            return self.protocol.begin()
        failures = []
        for device in self.context.getDeviceList(skip_on_error=True):
            if device.getVendorID() != 0x0451 or device.getProductID() not in (0xE022, 0xE012):
                continue
            io = None
            try:
                io = open_device(device, self.module)
                protocol = NspireProtocol(io, device.getProductID() == 0xE022)
                protocol.begin()
                self.protocol = protocol
                return True
            except (OSError, self.module.USBError) as error:
                if io:
                    io.close()
                failures.append(str(error))
        if failures:
            raise OSError(
                "Calculator USB could not be opened: "
                + failures[0]
                + ". Check USB permissions in docs/linux.md and close other linking apps."
            )
        return False

    def close(self):
        try:
            super().close()
        finally:
            self.context.close()
