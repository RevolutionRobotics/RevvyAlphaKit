# SPDX-License-Identifier: GPL-3.0-only

import os
import traceback

from revvy.utils.functions import read_json
from revvy.utils.logger import get_logger


class Assets:
    def __init__(self):
        self._log = get_logger('Assets')
        self._files = {}

    def add_source(self, path):
        """
        Add a new folder to the asset sources

        @param path: the asset folder with an assets.json file inside
        """
        assets_json = os.path.join(path, 'assets.json')
        # noinspection PyBroadException
        try:
            manifest = read_json(assets_json)
            self._log('Loading assets from {}'.format(path))
            files = manifest['files']
            for category, assets in files.items():
                if category not in self._files:
                    self._files[category] = {}

                for asset_name, asset_path in assets.items():
                    if asset_name in self._files[category]:
                        self._log('{} shadows asset {}'.format(path, asset_name))

                    self._log('New asset: ({}) {}'.format(category, asset_name))
                    self._files[category][asset_name] = os.path.join(path, asset_path)
        except Exception:
            self._log('Skip loading assets from {}'.format(path))
            self._log(traceback.format_exc())

    def get_asset_file(self, category, name):
        return self._files[category][name]

    def category_loader(self, category):
        files = self._files[category]
        return lambda file: files[file]
