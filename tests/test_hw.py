from dirigo_ni_frame_grabber.dirigo_ni_frame_grabber import NIFrameGrabber, NIFrameGrabberConfig


cfg = NIFrameGrabberConfig(
    device_name = "img0"
)

fg = NIFrameGrabber(cfg)
fg.connect()

a=1