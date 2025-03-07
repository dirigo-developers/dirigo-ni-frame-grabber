from imaqbindings import Board as IMAQBoard

from dirigo.hw_interfaces.camera import FrameGrabber


class NIFrameGrabber(FrameGrabber):
    def __init__(self, device_name: str, **kwargs):
        self._board = IMAQBoard(device_name)

    def serial_write(self, message):
        self._board.session_serial_write(message)
    
    def serial_read(self):
        """
        Reads up to the specified termination character (e.g. \r).
        """
        message = self._board.session_serial_read()
        return message
    
    def serial_read_nbytes(self, nbytes):
        """
        Alternative read method that reads individual bytes and does not stop at 
        terminator characters.
        """
        message = self._board.session_serial_read_bytes(nbytes)
        return message


# For testing
if __name__ == "__main__":
    NIFrameGrabber(device_name="img0")