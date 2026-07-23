"""Shot boundary detection via PySceneDetect. Defines the base partition of the
Timeline before silence splitting and visual tagging are layered on."""


def detect_shots(video_path: str) -> list[tuple[float, float]]:
    """Returns contiguous [(start, end), ...] shots covering the full video."""
    from scenedetect import open_video, SceneManager
    from scenedetect.detectors import ContentDetector

    video = open_video(video_path)
    scene_manager = SceneManager()
    scene_manager.add_detector(ContentDetector())
    scene_manager.detect_scenes(video)
    scene_list = scene_manager.get_scene_list()

    if not scene_list:
        return [(0.0, video.duration.get_seconds())]

    return [(s.get_seconds(), e.get_seconds()) for s, e in scene_list]
