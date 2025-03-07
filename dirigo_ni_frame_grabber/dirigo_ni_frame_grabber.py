from imaqbindings import Board as IMAQBoard

from dirigo.hw_interfaces.camera import FrameGrabber


class NIFrameGrabber(FrameGrabber):
    def __init__(self, device_name: str, **kwargs):
        self._board = IMAQBoard(device_name)

    def write_serial(self, message):
        self._board.session_serial_write(message)
    
    def read_serial(self):
        message = self._board.session_serial_read()
        return message


# For testing
if __name__ == "__main__":
    NIFrameGrabber(device_name="img0")