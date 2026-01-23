import ctypes
from ctypes import POINTER, c_int8
from typing import ClassVar, List
import time

from pydantic import Field

from imaqbindings import Board as IMAQBoard
from imaqbindings import Buffer as IMAQBuffer
from imaqbindings import enumerations as Im

from dirigo import units
from dirigo.hw_interfaces.image_transport import FrameGrabberConfig, FrameGrabber
from dirigo.sw_interfaces.acquisition import AcquisitionProduct



_INTERFACE_TYPE_TO_MODEL: dict[int, str] = {
    0x1426: "PCI-1426",
    0x1427: "PCIe-1427",
    0x1428: "PCI-1428",
    0x1429: "PCIe-1429",
    0x1430: "PCIe-1430",
    0x1433: "PCIe-1433",
    0x1437: "PCIe-1437",
}


class NIFrameGrabberConfig(FrameGrabberConfig):
    """
    NI-IMAQ frame grabber transport.
    """
    vendor: str = Field(
        default="National Instruments",
        json_schema_extra={"ui": {"hidden": True}},
    )
    # Model is introspected by the device
    model: None = Field(
        default=None,
        json_schema_extra={"ui": {"hidden": True}},
    )
    device_name: str = Field(..., description="NI-IMAQ device name (e.g. img0).")


class NIFrameGrabber(FrameGrabber):
    config_model: ClassVar[type[FrameGrabberConfig]] = NIFrameGrabberConfig
    title = "NI-IMAQ Frame Grabber"

    def __init__(self, cfg: NIFrameGrabberConfig, **kwargs):
        super().__init__(cfg, **kwargs)

        self._device_name = cfg.device_name

        self._board: IMAQBoard | None = None
        self._buffers: List[IMAQBuffer] = []
        self._frames_per_buffer: int = 1
        self._buffers_transferred: int = 0
        self._streaming: bool = False

    def _introspect_identity(self) -> dict[str, str]:
        introspected = {}
        
        board = self._require_connected()
        
        iface = board.get_attribute(Im.DeviceInformation.IMG_ATTR_INTERFACE_TYPE)
        if iface not in _INTERFACE_TYPE_TO_MODEL:
            raise RuntimeError("Unsupported framegrabber model")
        introspected["model"] = _INTERFACE_TYPE_TO_MODEL[iface]

        d = board.get_attribute(Im.DeviceInformation.IMG_ATTR_GETSERIAL)
        introspected["serial"] = hex(d)[2:].upper()
        
        return introspected
    
    def _connect_impl(self) -> None:
        self._board = IMAQBoard(self._device_name)

    def _close_impl(self) -> None:
        if self._board is None:
            return

        # Stop acquisition if needed
        try:
            if self._streaming:
                self.stop_stream()
        except Exception:
            # keep going; we still want to release resources
            pass

        # Clear and drop references to buffers
        for buffer in self._buffers:
            buffer.close()
        self._buffers.clear()
        self._buffers_transferred = 0

        self._board.close()

        self._board = None

    def _require_connected(self) -> IMAQBoard:
        if not self.is_connected or self._board is None:
            raise RuntimeError(f"{type(self).__name__} is not connected. Call connect() first.")
        return self._board

    # ---- Serial control ----
    def serial_write(self, message: str) -> None:
        board = self._require_connected()
        board.session_serial_write(message)

    def serial_read(self, nbytes: int | None = None) -> str:
        board = self._require_connected()
        if nbytes is None:
            return board.session_serial_read()
        return board.session_serial_read_bytes(nbytes)
    
    # ---- ImageTransport API ----
    @property
    def acq_width(self) -> int:
        board = self._require_connected()
        return board.get_attribute(Im.SessionInformation.IMG_ATTR_ACQWINDOW_WIDTH)

    @property
    def acq_height(self) -> int:
        board = self._require_connected()
        return board.get_attribute(Im.SessionInformation.IMG_ATTR_ACQWINDOW_HEIGHT)
    
    @property
    def bytes_per_pixel(self) -> int:
        board = self._require_connected()
        return board.get_attribute(Im.Image.IMG_ATTR_BYTESPERPIXEL)

    # ---- On-frame grabber board ROI ----
    @property
    def roi_width(self) -> int:
        board = self._require_connected()
        return board.get_attribute(Im.Image.IMG_ATTR_ROI_WIDTH)

    @roi_width.setter
    def roi_width(self, width: int) -> None:
        board = self._require_connected()
        board.set_attribute_2(Im.Image.IMG_ATTR_ROI_WIDTH, int(width))

    @property
    def roi_height(self) -> int:
        board = self._require_connected()
        return board.get_attribute(Im.Image.IMG_ATTR_ROI_HEIGHT)

    @roi_height.setter
    def roi_height(self, height: int) -> None:
        board = self._require_connected()
        board.set_attribute_2(Im.Image.IMG_ATTR_ROI_HEIGHT, int(height))

    @property
    def roi_left(self) -> int:
        board = self._require_connected()
        return board.get_attribute(Im.Image.IMG_ATTR_ROI_LEFT)

    @roi_left.setter
    def roi_left(self, left: int) -> None:
        board = self._require_connected()
        board.set_attribute_2(Im.Image.IMG_ATTR_ROI_LEFT, int(left))

    @property
    def roi_top(self) -> int:
        board = self._require_connected()
        return board.get_attribute(Im.Image.IMG_ATTR_ROI_TOP)

    @roi_top.setter
    def roi_top(self, top: int) -> None:
        board = self._require_connected()
        board.set_attribute_2(Im.Image.IMG_ATTR_ROI_TOP, int(top))

    # ---- Buffer management & streaming ----
    @property
    def frames_per_buffer(self) -> int:
        return self._frames_per_buffer

    @frames_per_buffer.setter
    def frames_per_buffer(self, frames: int) -> None:
        if not isinstance(frames, int) or frames <= 0:
            raise ValueError(f"Frames per buffer must be integer > 0, got {frames}")
        self._frames_per_buffer = int(frames)

    def prepare_buffers(self, nbuffers: int) -> None:
        board = self._require_connected()
        if self._streaming:
            raise RuntimeError("Cannot prepare buffers while streaming.")
        
        nframes = self.frames_per_buffer * nbuffers

        board.create_buf_list(nframes) # note: buffer list contains references for every frame

        self._buffers = []
        buffer_shape = (self.frames_per_buffer, self.roi_height, self.roi_width)
        for b in range(nbuffers):
            # Generate a contiguous memory area to store one buffer (of possibly multiple frames)
            buf = IMAQBuffer(
                board           = board,
                shape           = buffer_shape,
                bytes_per_pixel = self.bytes_per_pixel,
            )

            base_address = buf._adr

            # update buffer list (note: buffer list contains references for every frame)
            for s in range(self.frames_per_buffer):
                offset = s * self.bytes_per_frame
                buffer_ptr = ctypes.cast(base_address + offset, POINTER(c_int8))

                i = self.frames_per_buffer * b + s
                board.set_buffer_element_address(i, buffer_ptr)

                # configure ring buffer
                if i == nframes - 1:
                    cmd = Im.BufferCommand.IMG_CMD_LOOP 
                else:
                    cmd = Im.BufferCommand.IMG_CMD_NEXT
                board.set_buffer_element_command(i, cmd)

                board.set_buffer_element_size(i, self.bytes_per_frame)

            self._buffers.append(buf)
        
        board.session_configure()
        self._buffers_transferred = 0

    def start_stream(self) -> None:
        board = self._require_connected()
        if self._streaming:
            return
        self._buffers_transferred = 0
        board.session_acquire(async_flag=True)
        self._streaming = True

    def stop_stream(self) -> None:
        board = self._require_connected()
        if not self._streaming:
            return
        board.session_abort()
        self._streaming = False

    @property
    def buffers_acquired(self) -> int:
        board = self._require_connected()
        frames = board.get_attribute(Im.StatusInformation.IMG_ATTR_FRAME_COUNT)
        return frames // self.frames_per_buffer
    
    def get_next_completed_buffer(self, 
                                  product: AcquisitionProduct, 
                                  timeout: units.Time = units.Time("2 s")) -> None:
        """
        Copy the next completed *Dirigo buffer* into product.
        """
        t0 = time.perf_counter()
        while self.buffers_acquired <= self._buffers_transferred:
            if time.perf_counter() - t0 > timeout:
                raise TimeoutError("Did not recieve a full frame grabber product before timeout")
            time.sleep(0.010) # TODO shift to adaptive sleep times

        b = self._buffers_transferred % len(self._buffers)
        product.data[:] = self._buffers[b].buffer
        self._buffers_transferred += 1

