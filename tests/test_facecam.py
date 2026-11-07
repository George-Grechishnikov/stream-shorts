import numpy as np

from shorts.edit import facecam


def test_model_bundled_and_loads():
    assert facecam.MODEL.exists() and facecam.MODEL.stat().st_size > 100_000
    assert facecam._detector(320, 180) is not None


def test_stable_face_prefers_fixed_position_over_jumping_faces():
    W, H = 1920, 1080
    cam = [(1500, 780, 120, 150, 0.9)] * 6                       # стример: всегда на месте
    noise = [(x, y, 130, 160, 0.95) for x, y in [(100, 100), (900, 300), (400, 700), (1200, 50)]]
    face = facecam._stable_face(cam + noise, W, H, need=3)
    assert face is not None
    assert np.allclose(face, [1500, 780, 120, 150])


def test_stable_face_none_when_too_few():
    assert facecam._stable_face([(10, 10, 50, 50, 0.9)] * 2, 1920, 1080, need=3) is None
    assert facecam._stable_face([], 1920, 1080, need=3) is None


def test_no_faces_returns_none(tmp_path):
    import cv2

    p = tmp_path / "blank.mp4"
    vw = cv2.VideoWriter(str(p), cv2.VideoWriter_fourcc(*"mp4v"), 30, (640, 360))
    for i in range(30):
        vw.write(np.full((360, 640, 3), i * 5, np.uint8))
    vw.release()
    assert facecam.detect_cam(p) is None
