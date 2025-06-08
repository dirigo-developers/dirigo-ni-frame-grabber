import ctypes
from ctypes import POINTER, c_int8
from typing import Optional, List

from imaqbindings import Board as IMAQBoard
from imaqbindings import Buffer as IMAQBuffer
from imaqbindings import enumerations as Im

from dirigo import units
from dirigo.hw_interfaces.hw_interface import NoBuffers
from dirigo.sw_interfaces.acquisition import AcquisitionProduct
from dirigo.hw_interfaces.camera import FrameGrabber


class NIFrameGrabber(FrameGrabber):
    def __init__(self, device_name: str, **kwargs):
        self._board = IMAQBoard(device_name)
        self._lines_per_buffer: Optional[int] = None

    def serial_write(self, message):
        self._board.session_serial_write(message)
    
    def serial_read(self, nbytes: Optional[int] = None):
        """
        Reads up to nbytes. If nbytes is not specified, then reads up to the 
        specified termination character (e.g. \r).
        """
        if nbytes is None:
            message = self._board.session_serial_read()
        else:
            message = self._board.session_serial_read_bytes(nbytes)
        return message
    
    @property
    def pixels_width(self):
        return self._board.get_attribute(
            Im.SessionInformation.IMG_ATTR_ACQWINDOW_WIDTH
        )

    @property
    def roi_height(self):
        return self._board.get_attribute(Im.Image.IMG_ATTR_ROI_HEIGHT)

    @property
    def roi_width(self):
        return self._board.get_attribute(Im.Image.IMG_ATTR_ROI_WIDTH)
    
    @roi_width.setter
    def roi_width(self, width: int):
        # validate?
        self._board.set_attribute_2(Im.Image.IMG_ATTR_ROI_WIDTH, width)

    @property
    def roi_left(self):
        return self._board.get_attribute(Im.Image.IMG_ATTR_ROI_LEFT)
    
    @roi_left.setter
    def roi_left(self, left: int):
        # validate?
        self._board.set_attribute_2(Im.Image.IMG_ATTR_ROI_LEFT, left)

    @property
    def lines_per_buffer(self) -> int:
        # For line scan acquisitions, lines_per_buffer must be set, for 2D, use ROI height
        if self._lines_per_buffer is None:
            return self.roi_height
        else:
            return self._lines_per_buffer

    @lines_per_buffer.setter
    def lines_per_buffer(self, lines: int):
        if self.roi_height != 1:
            raise RuntimeError("Can not set lines per buffer for area camera")
        self._lines_per_buffer = lines

    @property
    def subbuffers_per_buffer(self) -> int:
        if self.roi_height == 1: # line camera
            return self.lines_per_buffer
        else:
            return 1
    @property
    def bytes_per_pixel(self):
        return self._board.get_attribute(Im.Image.IMG_ATTR_BYTESPERPIXEL)
    
    def prepare_buffers(self, nbuffers: int):

        # Create the buffer list: 1 for each frame with area cameras, 1 for each
        # line with line cameras (Dirigo API considers a 'buffer' to be a set of 
        # lines, different from IMAQ which considers each exposure a buffer)
        if self.roi_height == 1: # line camera
            if self._lines_per_buffer is None:
                raise RuntimeError("Lines per buffer not initialized")
            n = nbuffers * self._lines_per_buffer
        else:
            n = nbuffers
        self._board.create_buf_list(n)

        # Create buffers in memory. This is a single NI-allocated block shaped 
        # as (lines_per_buffer, roi_width).
        # b: Dirigo buffer index
        # s: sub-buffer index (within each Dirigo buffer)
        # i: IMAQ buffer index (list of all the sub-buffers)
        self._buffers: List[IMAQBuffer] = []
        for b in range(nbuffers):
            buf = IMAQBuffer(
                board=self._board,
                shape=(self.lines_per_buffer, self.roi_width),
                bytes_per_pixel=self.bytes_per_pixel
            )

            base_address = buf._adr # start of the superbuffer
            
            # for each sub-buffer ...
            for s in range(self.subbuffers_per_buffer):
                # compute sub-buffer address, provide pointer
                offset = s * (self.bytes_per_buffer // self.subbuffers_per_buffer)
                buffer_ptr = ctypes.cast(base_address + offset, POINTER(c_int8))
                
                i = self.subbuffers_per_buffer * b + s
                self._board.set_buffer_element_address(i, buffer_ptr)

                # set sub-buffer command
                if i == (nbuffers * self.subbuffers_per_buffer - 1):
                    #cmd = Im.BufferCommand.IMG_CMD_STOP
                    cmd = Im.BufferCommand.IMG_CMD_LOOP
                else:
                    cmd = Im.BufferCommand.IMG_CMD_NEXT

                self._board.set_buffer_element_command(i, cmd)

                # set buffer size
                self._board.set_buffer_element_size(
                    i, 
                    self.bytes_per_buffer // self.subbuffers_per_buffer
                ) 

            self._buffers.append(buf)

        self._board.session_configure()

    def start(self):
        self._buffers_transferred = 0
        self._board.session_acquire(async_flag=True)

    def stop(self):
        i = self._board.get_attribute(Im.StatusInformation.IMG_ATTR_FRAME_COUNT)
        print(f"Stopping acquisition, acquired {i} frames")
        self._board.session_abort()

    def get_next_completed_buffer(self, acquisition_product: AcquisitionProduct):
        """
        Copy the next completed buffer into acquisition_product.
        
        Raises NoCompletedBuffers exception if no new buffers are available.
        """
        if self.buffers_acquired <= self._buffers_transferred:
            raise NoBuffers

        b = self._buffers_transferred % len(self._buffers)
        print("Copying from buffer", b)

        acquisition_product.data[:] = self._buffers[b].buffer
        self._buffers_transferred += 1

    @property
    def buffers_acquired(self):
        i = self._board.get_attribute(Im.StatusInformation.IMG_ATTR_FRAME_COUNT)
        return i // self.subbuffers_per_buffer

    @property
    def data_range(self) -> units.IntRange:
        if self._camera is None:
            raise RuntimeError("Camera not initialized")
        return self._camera.data_range