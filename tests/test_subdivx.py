# -*- coding: utf-8 -*-
from babelfish import Language
import os
import pytest
from subliminal.exceptions import AuthenticationError, ConfigurationError
from subliminal.providers.subdivx import SubdivxSubtitle, SubdivxProvider
from vcr import VCR

vcr = VCR(path_transformer=lambda path: path + '.yaml',
          record_mode=os.environ.get('VCR_RECORD_MODE', 'once'),
          match_on=['method', 'scheme', 'host', 'port', 'path', 'query', 'body'],
          cassette_library_dir=os.path.join('tests', 'cassettes', 'subdivx'))


def test_get_matches_release_group(episodes):
    subtitle = SubdivxSubtitle(Language('es'), None, None, 'The.Big.Bang.Theory.S07E05.720p.x264-dimension.mkv', None, None, None )
    matches = subtitle.get_matches(episodes['bbt_s07e05'])
    assert matches == {'series', 'season', 'episode', 'release_group'}


def test_get_matches_resolution_release_group(episodes):
    subtitle = SubdivxSubtitle(Language('es'), None, None, 'The.Big.Bang.Theory.S07E05.720p.x264-dimension.mkv', None, None, None )
    matches = subtitle.get_matches(episodes['bbt_s07e05'])
    assert matches == {'series', 'season', 'episode', 'release_group', 'resolution'}


def test_get_matches_no_match(episodes):
    subtitle = SubdivxSubtitle(Language('es'), None, None, 'The.Big.Bang.Theory.S07E05.720p.x264-dimension.mkv', None, None, None )
    matches = subtitle.get_matches(episodes['house_of_cards_us_s06e01'])
    assert matches == set()


def test_configuration_error_no_username():
    with pytest.raises(ConfigurationError):
        Addic7edProvider(password='subdivx')


def test_configuration_error_no_password():
    with pytest.raises(ConfigurationError):
        Addic7edProvider(username='subliminalsubdivx')


@pytest.mark.integration
@vcr.use_cassette
def test_login():
    provider = SubdivxProvider('subliminalsubdivx', 'subdivx')
    assert provider.logged_in is False
    provider.initialize()
    assert provider.logged_in is True


@pytest.mark.integration
@vcr.use_cassette
def test_login_bad_password():
    provider = SubdivxProvider('subliminalsubdivx', 'lanimilbus')
    with pytest.raises(AuthenticationError):
        provider.initialize()


@pytest.mark.integration
@vcr.use_cassette
def test_logout():
    provider = SubdivxProvider('subliminalsubdivx', 'subdivx')
    provider.initialize()
    provider.terminate()
    assert provider.logged_in is False


@pytest.mark.integration
@vcr.use_cassette
def test_query(episodes):
    video = episodes['bbt_s07e05']
    with SubdivxProvider() as provider:
        subtitles = provider.query(video.series+".S"+str(video.season).zfill(2)+"E"+str(video.episode).zfill(2))
    assert len(subtitles) > 0
    for subtitle in subtitles:
        assert subtitle.series == video.series
        assert subtitle.season == video.season
        assert subtitle.year is None


@pytest.mark.integration
@vcr.use_cassette
def test_download_subtitle(episodes):
    video = episodes['bbt_s07e05']
    languages = {Language('es')}
    with SubdivxProvider() as provider:
        subtitles = provider.list_subtitles(video, languages)
        provider.download_subtitle(subtitles[0])
    assert subtitles[0].content is not None
    assert subtitles[0].is_valid() is True
