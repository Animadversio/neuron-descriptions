"""Unit tests for lv/models/featurizers module."""
from lv.models import featurizers
from tests import conftest

import pytest
import torch


class FakeFeaturizer(featurizers.Featurizer):
    """A fake Featurizer that always returns zeros."""

    def __init__(self, feature_shape):
        """Initialize the featurizer."""
        super().__init__()
        self.feature_shape = feature_shape

    def forward(self, images, masks, **kwargs):
        """Assert on inputs and return zeros."""
        assert not kwargs
        assert images.shape[0] == masks.shape[0]
        assert images.shape[2:] == masks.shape[2:]
        assert images.shape[1] == 3
        assert masks.shape[1] == 1
        return torch.zeros(len(images), *self.feature_shape)


FEATURE_SHAPE = (10, 10)


@pytest.fixture
def featurizer():
    """Return a FakeFeaturizer for testing."""
    return FakeFeaturizer(FEATURE_SHAPE)


@pytest.mark.parametrize('device', (None, 'cpu', torch.device('cpu')))
def test_featurizer_map(featurizer, top_images_dataset, device):
    """Test Featurizer.map returns TensorDataset of right size."""
    actual = featurizer.map(top_images_dataset,
                            image_index=-2,
                            mask_index=-1,
                            display_progress=False,
                            device=device)
    assert len(actual) == len(top_images_dataset)
    for (features,) in actual:
        assert features.shape == (conftest.N_TOP_IMAGES_PER_UNIT,
                                  *FEATURE_SHAPE)
        assert features.eq(0).all()


def test_pretrained_pyramid_featurizer_init_bad_config():
    """Test PretrainedPyramidFeaturizer.__init__ dies on bad config."""
    bad = 'bad-config'
    with pytest.raises(ValueError, match=f'.*{bad}.*'):
        featurizers.PretrainedPyramidFeaturizer(config=bad)


BATCH_SIZE = 10
IMAGE_SIZE = 224
IMAGE_SHAPE = (3, IMAGE_SIZE, IMAGE_SIZE)
MASK_SHAPE = (1, IMAGE_SIZE, IMAGE_SIZE)


@pytest.fixture
def images():
    """Return fake images for testing."""
    return torch.rand(BATCH_SIZE, *IMAGE_SHAPE)


@pytest.fixture
def masks():
    """Return fake masks for testing."""
    return torch.randint(2, size=(BATCH_SIZE, *MASK_SHAPE))


@pytest.mark.parametrize('config', ('resnet18', 'alexnet'))
def test_pretrained_pyramid_featurizer_forward(config, images, masks):
    """Test PretrainedPyramidFeaturizer.forward returns correct shape."""
    featurizer = featurizers.PretrainedPyramidFeaturizer(config=config,
                                                         pretrained=False)
    actual = featurizer(images, masks)
    assert actual.shape == (BATCH_SIZE, *featurizer.feature_shape)
    assert not torch.isnan(actual).any()


@pytest.mark.parametrize('config', ('resnet18', 'alexnet'))
def test_pretrained_pyramid_featurizer_forward_invalid_mask(
        config, images, masks):
    """Test PretrainedPyramidFeaturizer.forward handles some invalid masks."""
    featurizer = featurizers.PretrainedPyramidFeaturizer(config=config,
                                                         pretrained=False)
    masks[-2:] = 0
    actual = featurizer(images, masks)
    assert actual.shape == (BATCH_SIZE, *featurizer.feature_shape)
    assert actual[-2:].eq(0).all()
    assert not actual[:-2].eq(0).all()
    assert not torch.isnan(actual).any()


@pytest.mark.parametrize('config', ('resnet18', 'alexnet'))
def test_pretrained_pyramid_featurizer_forward_all_invalid_masks(
        config, images, masks):
    """Test PretrainedPyramidFeaturizer.forward handles all invalid masks."""
    featurizer = featurizers.PretrainedPyramidFeaturizer(config=config,
                                                         pretrained=False)
    actual = featurizer(images, torch.zeros_like(masks))
    assert actual.shape == (BATCH_SIZE, *featurizer.feature_shape)
    assert actual.eq(0).all()
