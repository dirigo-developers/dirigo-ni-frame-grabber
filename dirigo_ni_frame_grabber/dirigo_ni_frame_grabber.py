import ctypes
from ctypes import POINTER, c_int8
from typing import Optional

from imaqbindings import Board as IMAQBoard
from imaqbindings import Buffer as IMAQBuffer
from imaqbindings import enumerations as Im

from dirigo.hw_interfaces.camera import FrameGrabber


class NIFrameGrabber(FrameGrabber):
    def __init__(self, device_name: str, **kwargs):
        self._board = IMAQBoard(device_name)

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
    def bytes_per_pixel(self):
        return self._board.get_attribute(Im.Image.IMG_ATTR_BYTESPERPIXEL)
    
    def prepare_buffers(self, nbuffers: int):
        """
        Creates a contiguous "superbuffer" with shape (nbuffers, height, width).
        Then sets up each buffer-list element to point into that block at the 
        correct offset.
        """
        # create the buffer list
        self._board.create_buf_list(nbuffers)

        # Create the super-buffer. This is a single NI-allocated block shaped as (n_buffers, roi_height, roi_width).
        self.super_buffer = IMAQBuffer(
            board=self._board,
            shape=(nbuffers, self.roi_height, self.roi_width),
            bytes_per_pixel=self.bytes_per_pixel
        )

        base_address = self.super_buffer._adr # start of the superbuffer
        # for each sub-buffer ...
        for i in range(nbuffers):
            # compute sub-buffer address, provide pointer
            offset = i * self.bytes_per_buffer
            buffer_ptr = ctypes.cast(base_address + offset, POINTER(c_int8))
            self._board.set_buffer_element_address(i, buffer_ptr)

            # set sub-buffer command
            if i == (nbuffers - 1):
                cmd = Im.BufferCommand.IMG_CMD_STOP
            else:
                cmd = Im.BufferCommand.IMG_CMD_NEXT
            self._board.set_buffer_element_command(i, cmd)

            # set buffer size
            self._board.set_buffer_element_size(i, self.bytes_per_buffer) 

        self._board.session_configure()

    def start(self):
        self._board.session_acquire(async_flag=True)

    def stop(self):
        self._board.session_abort()

    def get_next_completed_superbuffer(self):
        return self.super_buffer.buffer

    @property
    def buffers_acquired(self):
        return self._board.get_attribute(Im.StatusInformation.IMG_ATTR_FRAME_COUNT)

# For testing
if __name__ == "__main__":
    NIFrameGrabber(device_name="img0")