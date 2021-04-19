"""Models that map images and masks to features."""
from typing import Any, Callable, Mapping, Optional, Sequence, Tuple, Union

from lv.ext.torchvision import models
from lv.utils.typing import Device
from third_party.netdissect import nethook

import torch
import tqdm
from torch import nn
from torch.nn import functional
from torch.utils import data


class Featurizer(nn.Module):
    """An abstract module mapping images and masks to features."""

    feature_shape: Tuple[int, ...]

    def forward(self, images: torch.Tensor, masks: torch.Tensor,
                **kwargs: Any) -> torch.Tensor:
        """Compute masked image features."""
        raise NotImplementedError

    def map(self,
            dataset: data.Dataset,
            image_index: Union[int, str] = -3,
            mask_index: Union[int, str] = -2,
            batch_size: int = 128,
            device: Optional[Device] = None,
            display_progress: bool = True,
            **kwargs: Any) -> data.TensorDataset:
        """Featurize an entire dataset.

        Keyword arguments are passed to `forward`.

        Args:
            dataset (data.Dataset): The dataset to featurize. Should return
                a sequence or mapping of values.
            image_index (int, optional): Index of image in each dataset
                sample. Defaults to -3 to be compatible with
                AnnotatedTopImagesDataset.
            mask_index (int, optional): Index of mask in each dataset
                sample. Defaults to -2 to be compatible with
                AnnotatedTopImagesDataset.
            batch_size (int, optional): Featurize images in batches of this
                size. Defaults to 128.
            device (Optional[Device], optional): Run preprocessing on this
                device. Defaults to None.
            display_progress (bool, optional): Show progress bar.
                Defaults to True.

        Raises:
            ValueError: If images or masks are not tensors.

        Returns:
            data.TensorDataset: Dataset of image features.

        """
        if device is not None:
            self.to(device)

        mapped = []

        loader = data.DataLoader(dataset, batch_size=batch_size)
        for batch in tqdm.tqdm(loader) if display_progress else loader:
            images = batch[image_index]
            if not isinstance(images, torch.Tensor):
                raise ValueError(f'non-tensor images: {type(images).__name__}')
            if device is not None:
                images = images.to(device)

            masks = batch[mask_index]
            if not isinstance(masks, torch.Tensor):
                raise ValueError(f'non-tensor masks: {type(masks).__name__}')
            if device is not None:
                masks = masks.to(device)

            with torch.no_grad():
                features = self(images, masks, **kwargs)

            mapped.append(features)

        return data.TensorDataset(torch.cat(mapped))


FeaturizerFactory = Callable[..., nn.Sequential]
FeaturizerLayers = Sequence[str]
FeatureSize = int
FeaturizerConfig = Tuple[FeaturizerFactory, FeaturizerLayers, FeatureSize]


class PretrainedPyramidFeaturizer(Featurizer):
    """Map images and masks to a pyramid of masked convolutional features.

    Images are fed to a pretrained image classifier trained on ImageNet.
    The convolutional features from each layer are then masked using the
    downsampled mask, pooled, and stacked to create a feature vector.
    """

    @staticmethod
    def configs() -> Mapping[str, FeaturizerConfig]:
        """Return the support configs mapped to their names."""
        return {
            'alexnet': (
                models.alexnet_seq,
                ('conv1', 'conv2', 'conv3', 'conv4', 'conv5'),
                1152,
            ),
            'resnet18': (
                models.resnet18_seq,
                ('conv1', 'layer1', 'layer2', 'layer3', 'layer4'),
                1024,
            ),
        }

    def __init__(self, config: str = 'resnet18', **kwargs: Any):
        """Initialize the featurizer.

        Keyword arguments are forwarded to the constructor of the underlying
        classifier model.

        Args:
            config (str, optional): The featurizer config to use.
                See `PretrainedPyramidFeaturizer.configs` for options.
                Defaults to 'resnet18'.

        """
        super().__init__()

        configs = PretrainedPyramidFeaturizer.configs()
        if config not in configs:
            raise ValueError(f'featurizer not supported: {config}')

        factory, layers, feature_size = configs[config]

        kwargs.setdefault('pretrained', True)
        self.featurizer = nethook.InstrumentedModel(factory(**kwargs))
        self.featurizer.retain_layers(layers)

        self.layers = layers
        self.feature_shape = (feature_size,)

    def forward(self, images: torch.Tensor, masks: torch.Tensor,
                **_: Any) -> torch.Tensor:
        """Construct masked image features.

        Args:
            images (torch.Tensor): The images. Expected shape is
                (batch_size, 3, height, width).
            masks (torch.Tensor): The image masks. Expected shape is
                (batch_size, 1, height, width).

        Returns:
            torch.Tensor: Image features. Will have shape
                (batch_size, feature_size). Exact feature_size depends on
                the config.

        """
        result = images.new_zeros(len(images), *self.feature_shape)

        # If any masks are all zeros, we'll end up with divide-by-zero errors
        # when we try to normalize down the road. We'll simply set the feature
        # vectors for those images to 0.
        valid = ~masks.eq(0).all(dim=-1).all(dim=-1).view(-1)
        if not valid.any():
            return result
        indices = valid.nonzero().squeeze()
        images = images[indices]
        masks = masks[indices].float()

        # Feed images to featurizer, letting nethook record layer activations.
        self.featurizer(images)
        features = self.featurizer.retained_features(clear=True).values()

        # Mask the features at each level of the pyramid.
        masked = []
        for fs in features:
            ms = functional.interpolate(masks,
                                        size=fs.shape[-2:],
                                        mode='bilinear',
                                        align_corners=False)
            ms /= ms.sum(dim=(-1, -2), keepdim=True)
            mfs = fs.mul(ms).sum(dim=(-1, -2))
            masked.append(mfs)

        # All images with non-trivial masks will get real feature vectors.
        # The rest will be zero.
        result[indices] = torch.cat(masked, dim=-1)

        return result