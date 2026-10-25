import random

import pytest


@pytest.fixture(autouse=True)
def deterministic_random():
    random.seed(1234)
