from __future__ import absolute_import

import os

from pony.orm import db_session

from twisted.internet.defer import inlineCallbacks

from Tribler.Core.Modules.MetadataStore.serialization import ChannelMetadataPayload
from Tribler.Core.TorrentDef import TorrentDef
from Tribler.Test.test_as_server import TestAsServer
from Tribler.Test.tools import trial_timeout
from Tribler.pyipv8.ipv8.database import database_blob

DATA_DIR = os.path.join(os.path.abspath(os.path.dirname(os.path.realpath(__file__))), '..', '..', 'data')
CHANNEL_DIR = os.path.join(DATA_DIR, 'sample_channel')
CHANNEL_TORRENT = os.path.join(CHANNEL_DIR, 'channel.torrent')
CHANNEL_METADATA = os.path.join(CHANNEL_DIR, 'channel.mdblob')
CHANNEL_TORRENT_UPDATED = os.path.join(CHANNEL_DIR, 'channel_upd.torrent')
CHANNEL_METADATA_UPDATED = os.path.join(CHANNEL_DIR, 'channel_upd.mdblob')


class TestChannelDownload(TestAsServer):
    def setUpPreSession(self):
        super(TestChannelDownload, self).setUpPreSession()
        self.config.set_chant_enabled(True)
        self.config.set_libtorrent_enabled(True)

    @trial_timeout(20)
    @inlineCallbacks
    def test_channel_update_and_download(self):
        """
        Test whether we can successfully update a channel and download the new version
        """
        # First we have to manually add the old version
        old_payload = ChannelMetadataPayload.from_file(CHANNEL_METADATA)
        with db_session:
            old_channel = self.session.lm.mds.ChannelMetadata.from_payload(old_payload)
            chan_dir = os.path.join(CHANNEL_DIR, old_channel.dirname)

        self.session.lm.mds.process_channel_dir(chan_dir, old_payload.public_key, old_payload.id_)

        channel_tdef = TorrentDef.load(CHANNEL_TORRENT_UPDATED)
        libtorrent_port = self.get_port()
        yield self.setup_seeder(channel_tdef, CHANNEL_DIR, libtorrent_port)

        payload = ChannelMetadataPayload.from_file(CHANNEL_METADATA_UPDATED)
        # Download the channel in our session
        with db_session:
            self.session.lm.mds.process_payload(payload)
            channel = self.session.lm.mds.ChannelMetadata.get(signature=payload.signature)

        def fake_get_metainfo(infohash, timeout=30):
            return {b'info': {b'name': channel.dirname.encode('utf-8')}}

        self.session.lm.ltmgr.get_metainfo = fake_get_metainfo
        # The leecher should be hinted to leech from localhost. Thus, we must extend start_downoload_from_tdef
        # and get_metainfo to provide the hint.
        original_start_download_from_tdef = self.session.start_download_from_tdef

        def hinted_start_download(torrent_definition, download_config=None, hidden=False, original_call=True):
            download = original_start_download_from_tdef(torrent_definition, download_config, hidden)
            download.add_peer(("127.0.0.1", self.seeder_session.config.get_libtorrent_port()))
            return download

        self.session.start_download_from_tdef = hinted_start_download
        yield self.session.lm.gigachannel_manager.download_channel(channel)
        yield self.session.lm.gigachannel_manager.process_queued_channels()

        with db_session:
            # There should be 4 torrents + 1 channel torrent
            channel2 = self.session.lm.mds.ChannelMetadata.get(public_key=database_blob(payload.public_key))
            self.assertEqual(5, len(list(self.session.lm.mds.TorrentMetadata.select())))
            self.assertEqual(1562257279010, channel2.timestamp)
            self.assertEqual(channel2.timestamp, channel2.local_version)
