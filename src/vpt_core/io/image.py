import os
from dataclasses import dataclass
from typing import Dict, List, Set, Tuple, Union, Optional

import numpy as np
import rasterio

from vpt_core.image.filter_factory import create_filter_by_sequence
from vpt_core.io.vzgfs import get_rasterio_environment, rasterio_open
from vpt_core.segmentation.segmentation_task import SegTask


@dataclass(frozen=True)
class ImageInfo:
    channel: str
    z_layer: int
    full_path: Union[str, os.PathLike]


class ImageSet(Dict[str, Dict[int, np.ndarray]]):
    def z_levels(self) -> Set[int]:
        return set().union(*self.values())

    def as_list(self, key: str) -> List[np.ndarray]:
        return list(self.get(key, {}).values())

    def as_stack(self, order: Optional[List[str]] = None):
        if not order:
            return np.array([np.stack([z_stack[z] for z_stack in self.values()], axis=-1) for z in self.z_levels()])
        return np.array([np.stack([self[k][z] for k in order], axis=-1) for z in self.z_levels()])


def read_tile(window: Tuple[int, int, int, int], path: str, num_tries: int = 5) -> np.ndarray:
    num_retries = num_tries - 1

    for try_number in range(1, num_tries + 1):
        try:
            with get_rasterio_environment(path):
                with rasterio_open(path) as file:
                    image = file.read(
                        1,
                        window=rasterio.windows.Window(window[0], window[1], window[2], window[3]),
                    )
        except OSError:
            if try_number + 1 <= num_tries:
                print(f"Failed to read {path} at {window}. Retrying {try_number}/{num_retries}.")
            continue
        break
    else:
        raise IOError(f"Failed to read {path} at {window}")

    return np.squeeze(image)


def get_segmentation_images(images_info: List[ImageInfo], window_info: Tuple[int, int, int, int]) -> ImageSet:
    images = ImageSet()
    for image_info in images_info:
        if not images.get(image_info.channel):
            images[image_info.channel] = {}
        images[image_info.channel][image_info.z_layer] = read_tile(window_info, str(image_info.full_path))
    return images


def get_prepared_images(task: SegTask, images: ImageSet) -> Tuple[ImageSet, Tuple[float, float]]:
    cur_images = ImageSet()
    scale = None
    for input_info in task.task_input_data:
        image_filter = create_filter_by_sequence(input_info.image_preprocessing)
        task_z = sorted([z for z in images[input_info.image_channel] if z in task.z_layers])
        task_images = [images[input_info.image_channel][z] for z in task_z]
        if len(task_z) == 0:
            raise ValueError(
                f'Invalid experiment: no "{input_info.image_channel}" input images found for the task {task.task_id}'
            )
        filtered_images = image_filter(task_images)
        cur_scale = [
            (im.shape[0] / cur_im.shape[0], im.shape[1] / cur_im.shape[1])
            for im, cur_im in zip(task_images, filtered_images)
        ]
        if min(cur_scale) != max(cur_scale) or (scale is not None and scale != cur_scale[0]):
            raise ValueError(
                "Invalid preprocessing scale: input images for segmentation after postprocessing "
                "should have same sizes"
            )
        scale = cur_scale[0]
        cur_images[input_info.image_channel] = {z: filtered_images[i] for i, z in enumerate(task_z)}
    if scale is None:
        raise ValueError(f"Invalid experiment: no input images found for the task {task.task_id}")

    return cur_images, scale
