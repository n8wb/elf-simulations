"""Testing for Config class"""
from __future__ import annotations  # types are strings by default in 3.11

import unittest

import elfpy.simulators as simulators


class TestConfig(unittest.TestCase):
    """Test usage of the Config class"""

    def test_config_cant_add_new_attribs(self):
        """config object can't add new attributes after it's initialized"""
        config = simulators.Config()
        with self.assertRaises(AttributeError):
            config.new_attrib = 1

    def test_config_cant_change_existing_attribs(self):
        """config object can change existing attributes, only if not frozen"""
        config = simulators.Config()
        # change an existing attribute
        config.num_blocks_per_day = 2
        # freeze config
        config.freeze()  # pylint: disable=no-member # type: ignore
        # now you can't change attributes
        with self.assertRaises(AttributeError):
            config.num_blocks_per_day = 2
