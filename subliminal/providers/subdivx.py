# -*- coding: utf-8 -*-
import base64
import logging
import os
import re
import zlib
import html
import io
import urllib.parse

from babelfish import Language, language_converters
from guessit import guessit
from requests import Session
from rarfile import RarFile, is_rarfile
from zipfile import ZipFile, is_zipfile

from . import ParserBeautifulSoup, Provider, TimeoutSafeTransport
from .. import __short_version__
from ..exceptions import AuthenticationError, ConfigurationError, DownloadLimitExceeded, ProviderError
from ..subtitle import Subtitle, fix_line_ending
from ..utils import sanitize
from ..video import Episode, Movie

logger = logging.getLogger(__name__)
# language_converters.register('subdivx = subliminal.converters.subdivx:SubdivxConverter')

MY_SUBTITLE_EXTENSIONS = ('.srt', '.sub', '.ssa', '.ass')
MAIN_SUBDIVX_URL = "http://www.subdivx.com/"
SEARCH_PAGE_URL = MAIN_SUBDIVX_URL + \
    "index.php?accion=5&masdesc=&oxdown=1&pg=%(page)s&buscar=%(query)s"
PAGE_ENCODING = 'latin1'
subtitle_re = re.compile(r'''<a\s+class="titulo_menu_izq2?"\s+href="https?://www\.subdivx\.com/(?P<subtitle_id>.+?)\.html">(Subtitulo\s+de\s+)?(?P<video_name>.+?)</a></div><img.+?/></div><div\sid="buscador_detalle">\n<div\s+id="buscador_detalle_sub">(?P<description>[\s\S]+?)</div><div\s+id="buscador_detalle_sub_datos"><b>Downloads:</b>(?P<downloads>.+?)<b>Cds:</b>.+?<b>Subido\spor:</b>\s*<a.+?>(?P<uploader>.+?)</a>.+?<a.+?href="(?P<subtitle_url>.+?)"\srel="nofollow"\starget="new"><img.+?</a></div></div>''', re.DOTALL)
series_re = re.compile(r"""((?P<serie_name_b>.*)[ .]\((?P<year>\d{4})\)[ .][Ss](?P<season_b>\d{1,2})[Ee](?P<episode_b>\d{1,2})|(?P<serie_name_a>.*)[ .][Ss](?P<season_a>\d{1,2})[Ee](?P<episode_a>\d{1,2}))""")
series_filename_re = re.compile(r"""((?P<serie_name_b>.*)[ .](?P<year>\d{4})[ .][Ss](?P<season_b>\d{1,2})[Ee](?P<episode_b>\d{1,2}).*|(?P<serie_name_a>.*)[ .][Ss](?P<season_a>\d{1,2})[Ee](?P<episode_a>\d{1,2}).*)""")

import requests.packages.urllib3.util.ssl_
requests.packages.urllib3.util.ssl_.DEFAULT_CIPHERS += 'HIGH:!DH:!aNULL'

class SubdivxSubtitle(Subtitle):
    """Subdivx Subtitle."""
    provider_name = 'subdivx'
    # name_re = re.compile(r'^"(?P<series_name>.*)" (?P<series_title>.*)$')


    def __init__(self, language, subtitle_url, subtitle_id, video_name, description, downloads, uploader):
        super(SubdivxSubtitle, self).__init__(language, subtitle_url)
        self.subtitle_id = subtitle_id
        self.video_name = video_name
        self.description = description
        self.downloads = downloads
        self.uploader = uploader
        self.subtitle_url = subtitle_url

    @property
    def movie_title(self):
        movie = re.findall(r"""(.*?[ .]\(\d{4})\)  # Title including year
                       [ .a-zA-Z]*     # Space, period, or words
                       (\d{3,4}p)?      # Quality
                    """, self.video_name, re.VERBOSE)
        if len(movie)>0:
            s = movie[0]
            logger.debug('"%s"' % str(s[0]))
            logger.debug('"%s"' % str(s[0][:-6]).replace(".", " "))
            return str(s[0][:-6]).replace(".", " ")
        else:
            return ''


    def movie_year(self):
        movie = re.findall(r"""(.*?[ .]\(\d{4})\)  # Title including year
                       [ .a-zA-Z]*     # Space, period, or words
                       (\d{3,4}p)?      # Quality
                    """, self.video_name, re.VERBOSE)

        # logger.debug('"%r"' % movie)
        if len(movie)>0:
            s = movie[0]
            logger.debug('"%s"' % str(s[0]))
            logger.debug('"%s"' % str(s[0][-4:]))
            return int(str(s[0][-4:]))
        else:
            return 0

    @property
    def series_name(self):
        groups = series_re.match(self.video_name)

        if groups.group('year') is None:
            s = groups.group('serie_name_a')
        else:
            s = groups.group('serie_name_b')
        # series = re.findall(r"""(.*)          # Title
        #                 [ .]
        #                 [Ss](\d{1,2})    # Season
        #                 [Ee](\d{1,2})    # Episode
        #             """, self.video_name, re.VERBOSE)
        # s = series[0]
        logger.debug('series_name "%s"' % str(s).replace(".", " "))
        return str(s).replace(".", " ")

    @property
    def series_season(self):
        groups = series_re.match(self.video_name)

        if groups.group('year') is None:
            s = groups.group('season_a')
        else:
            s = groups.group('season_b')
        # series = re.findall(r"""(.*)          # Title
        #                 [ .]
        #                 [Ss](\d{1,2})    # Season
        #                 [Ee](\d{1,2})    # Episode
        #             """, self.video_name, re.VERBOSE)
        # s = series[0]
        logger.debug('series_season "%s"' % s)
        return int(s)

    @property
    def series_episode(self):
        groups = series_re.match(self.video_name)

        if groups.group('year') is None:
            s = groups.group('episode_a')
        else:
            s = groups.group('episode_b')
        # series = re.findall(r"""(.*)          # Title
        #                 [ .]
        #                 [Ss](\d{1,2})    # Season
        #                 [Ee](\d{1,2})    # Episode
        #             """, self.video_name, re.VERBOSE)
        # s = series[0]
        logger.debug('series_episode "%s"' % s)
        return int(s)

    @property
    def series_year(self):
        groups = series_re.match(self.video_name)

        if groups.group('year') is None:
            s = None
        else:
            s = int(groups.group('year'))
        # series = re.findall(r"""(.*)          # Title
        #                 [ .]
        #                 [Ss](\d{1,2})    # Season
        #                 [Ee](\d{1,2})    # Episode
        #             """, self.video_name, re.VERBOSE)
        # s = series[0]
        logger.debug('series_year "%r"' % s)
        return s


    def is_release_group_found_in_description(self, release_group):
        # logger.debug('relase group: "%s"' % release_group)
        # logger.debug('description: "%s"' % self.description)
        if release_group.lower() in self.description.lower():
            # logger.debug('relase group found')
            return True
        else:
            return False

    def is_resolution_found_in_description(self, resolution):
        # logger.debug('resolution: "%s"' % resolution)
        # logger.debug('description: "%s"' % self.description)
        if resolution.lower() in self.description.lower():
            # logger.debug('resolution group found')
            return True
        else:
            return False

    def is_source_found_in_description(self, source):
        logger.debug('source: "%s"' % source)
        logger.debug('description: "%s"' % self.description)

        if source.lower() == 'web-dl':
            if any(word in self.description.lower() for word in ['web-dl','web dl','webdl']):
                logger.debug('source found')
                return True
            else:
                logger.debug('source NOT found')
                return False
        elif source.lower() == 'webrip':
            if any(word in self.description.lower() for word in ['web rip','webrip','web-rip']):
                logger.debug('source found')
                return True
            else:
                logger.debug('source NOT found')
                return False
        elif source.lower() == 'blu-ray':
            if any(word in self.description.lower() for word in ['bluray','bdrip','brrip']):
                logger.debug('source found')
                return True
            else:
                logger.debug('source NOT found')
                return False
        elif source.lower() in self.description.lower():
            logger.debug('source found')
            return True
        else:
            logger.debug('source NOT found')
            return False

    def guess_spanish_neutral(self):
        TRUSTED_LATINO_UPLOADERS = ['TaMaBin','oraldo','enanodog','antillan0','gozilla2','axel7902']
        LATINO_REFERENCES = ['neutro','Neutro','NEUTRO','latino','Latino','LATINO','Latinoamérica',
                            'latinoamérica','latinizado','latina','Latina','neutral']

        if self.uploader in TRUSTED_LATINO_UPLOADERS:
            return True
        elif any(word in self.description for word in LATINO_REFERENCES):
            return True
        else:
            return False

    def get_matches(self, video):
        matches = set()

        # episode
        if isinstance(video, Episode):
            # series
            if video.series and sanitize(self.series_name) == sanitize(video.series):
                matches.add('series')
            # year
            # if video.original_series and self.movie_year is None or video.year and video.year == self.movie_year:
            #     matches.add('year')
            # season
            if video.season and self.series_season == video.season:
                matches.add('season')
            # episode
            if video.episode and self.series_episode == video.episode:
                matches.add('episode')
            # title
            # if video.title and sanitize(self.video_title) == sanitize(video.title):
            #     matches.add('title')
            # release_group
            if (video.release_group and self.is_release_group_found_in_description(video.release_group)):
                matches.add('release_group')
            # resolution
            if (video.resolution and self.is_resolution_found_in_description(video.resolution)):
                matches.add('resolution')

            if (video.source and self.is_source_found_in_description(video.source)):
                matches.add('source')

            # Hack to prefer neutral spanish in case of a tie
            if(self.guess_spanish_neutral()):
                matches.add('video_codec')
            # TODO
            # format
            #if video.format and self.version and video.format.lower() in self.version.lower():
            #    matches.add('format')

            # guess
            # matches |= guess_matches(video, guessit(self.movie_release_name, {'type': 'episode'}))
            # matches |= guess_matches(video, guessit(self.filename, {'type': 'episode'}))

        # movie
        elif isinstance(video, Movie):
            # title
            logger.info('self.movie_title: %r ', sanitize(self.movie_title))
            logger.info('video.title: %r ', sanitize(video.title))

            if video.title and sanitize(self.movie_title) == sanitize(video.title):
                matches.add('title')
            # year
            if video.year and self.movie_year() == video.year:
                matches.add('year')
            # release_group
            if (video.release_group and self.is_release_group_found_in_description(video.release_group)):
                matches.add('release_group')
            # resolution
            if (video.resolution and self.is_resolution_found_in_description(video.resolution)):
                matches.add('resolution')

            if (video.source and self.is_source_found_in_description(video.source)):
                matches.add('source')

            # Hack to prefer neutral spanish in case of a tie
            if(self.guess_spanish_neutral()):
                matches.add('video_codec')
            # guess
            # matches |= guess_matches(video, guessit(self.movie_release_name, {'type': 'movie'}))
            # matches |= guess_matches(video, guessit(self.filename, {'type': 'movie'}))
            # hash

        # else:
        #     logger.info('%r is not a valid movie_kind', self.movie_kind)
        #     return matches


        return matches


class SubdivxProvider(Provider):
    """Subdivx Provider.
        :param str username: username.
        :param str password: password.

    """
    languages = {Language('spa', 'MX')} | {Language(l) for l in [
        'spa'
    ]}
    subtitle_class = SubdivxSubtitle
    server_url = 'https://www.subdivx.com/'
    video_types = (Episode,Movie)

    def __init__(self, username=None, password=None):
        if any((username, password)) and not all((username, password)) or username is None or password is None:
            raise ConfigurationError('Username and password must be specified')

        self.username = username
        self.password = password
        self.logged_in = False
        self.session = None

    def initialize(self):
        self.session = Session()
        # self.session.headers['User-Agent'] = 'Subliminal/%s' % __short_version__
        self.session.headers['User-Agent'] = self.user_agent
        # login
        if self.username and self.password:
            # logger.info('Logging in')
            data = {'usuario': self.username, 'clave': self.password,'Enviar':'Entrar','accion':'50','enviau':'1','refer':'https://www.subdivx.com/'}
            r = self.session.post('https://www.subdivx.com/index.php', data=data, timeout=10, verify=False)
            r.raise_for_status()

            # soup = ParserBeautifulSoup(r.content, ['html.parser'])
            # if soup.find('span', {'class': 'fuente6'}, string=re.compile(u'Nick o Password incorrectos')):
            #     raise AuthenticationError(self.username)
            #     logger.debug('Error %s',r.content)

            if re.match('.*Nick o Password incorrectos.*', r.text, re.DOTALL) is not None:
              raise AuthenticationError(self.username)

            logger.debug('Logged in')
            self.logged_in = True

    def terminate(self):
        # logout
        if self.logged_in:
            # logger.info('Logging out')
            r = self.session.get(self.server_url + 'index.php?abandon=1', allow_redirects=False, timeout=10, verify=False)
            r.raise_for_status()
            logger.debug('Logged out')
            self.logged_in = False

        self.session.close()


    def query(self, query=None):
        subs_list = []
        page = 1
        while True:
            # logger.debug('Trying page %d', page)
            url = SEARCH_PAGE_URL % {'page': page,
                                     'query': urllib.parse.quote_plus(query)}

            logger.debug('url %r', url)
            # get the page of the episode / movie
            r = self.session.get(url, timeout=10, verify=False)
            r.raise_for_status()

            soup = ParserBeautifulSoup(r.content, ['lxml', 'html.parser'])

            # logger.debug('"%r"' % r)
            # logger.debug('"%r"' % soup)

            # match = subtitle_re.search(str(soup))
            #
            # #for counter, match in enumerate(subtitle_re.finditer(str(soup))):
            # groups = match.groupdict()
            #
            # subtitle_id = groups['subtitle_id']
            # logger.debug('"%s"' % subtitle_id)
            #
            # video_name = groups['video_name']
            # logger.debug('"%s"' % video_name)
            #
            # description = groups['description']
            # logger.debug('"%s"' % description)
            #
            # dls = re.sub(r'[,.]', '', groups['downloads'])
            # downloads = int(dls)
            #
            #
            # uploader = groups['uploader']
            # logger.debug('"%s"' % uploader)
            #   #language = guess_kind_of_spanish(description, uploader)
            #
            # subtitle_url = groups['subtitle_url']
            # logger.debug('"%s"' % subtitle_url)
            if soup is None or not subtitle_re.search(str(soup)):
                break
            for counter, match in enumerate(subtitle_re.finditer(str(soup))):
                groups = match.groupdict()

                subtitle_id = groups['subtitle_id']
                logger.debug('"%s"' % subtitle_id)

                dls = re.sub(r'[,.]', '', groups['downloads'])
                downloads = int(dls)

                description = sanitize(groups['description'])
                logger.debug('"%s"' % description)

                uploader = groups['uploader']

                language = self.guess_language(description, uploader)
                logger.debug('"%s"' % language)

                # h = HTMLParser()
                subtitle_url = html.unescape(groups['subtitle_url'])
                logger.debug('"%s"' % subtitle_url)

                video_name = groups['video_name']
                logger.debug('"%s"' % video_name)

                # try:
                #     if not counter:
                #         logger.debug('Subtitles found for subdivx_id = %s:' % subtitle_id)
                # except Exception:
                #     pass

                subtitle = self.subtitle_class(language, subtitle_url, subtitle_id, video_name, description, downloads, uploader)
                logger.debug('Found subtitle %r', subtitle)

                subs_list.append(subtitle)
            page += 1

        return subs_list

    def guess_language(self, description, uploader):
        # TRUSTED_LATINO_UPLOADERS = ['TaMaBin','oraldo','enanodog','antillan0','gozilla2','axel7902']
        NON_LATINO_REFERENCES = ['españa','iberico','ibérico','castellano']
        NON_SPANISH_REFERENCES = ['ingles','inglés','francés','frances','portugues','portugués']
        LATINO_REFERENCES = ['neutro','Neutro','NEUTRO','latino','Latino','LATINO','Latinoamérica',
                            'latinoamérica','latinizado','latina','Latina','neutral']

        if any(word in description.lower() for word in LATINO_REFERENCES):
            return Language('spa','MX')
        elif any(word in description.lower() for word in NON_LATINO_REFERENCES):
            return Language('spa')
        elif any(word in description.lower() for word in NON_SPANISH_REFERENCES):
            return Language('eng')
        else:
            return Language('spa','MX')



    # def cleanup_subdivx_comment(comment):
    #     """Convert the subtitle comment HTML to plain text."""
    #     parser = html2text.HTML2Text()
    #     parser.unicode_snob = True
    #     parser.ignore_emphasis = True
    #     parser.ignore_tables = True
    #     parser.ignore_links = True
    #     parser.body_width = 1000
    #     clean_text = parser.handle(comment)
    #     # Remove new lines manually
    #     clean_text = re.sub('\n', ' ', clean_text)
    #     return clean_text.rstrip(' \t')

    def list_subtitles(self, video, languages):
        season = episode = None
        logger.debug('video.name %r', video.name)
        if isinstance(video, Episode):
            if self.series_video_name_has_year(video.name):
                query = video.series+"."+str(video.year)+".S"+str(video.season).zfill(2)+"E"+str(video.episode).zfill(2)
            else:
                query = video.series+".S"+str(video.season).zfill(2)+"E"+str(video.episode).zfill(2)


            # season = video.season
            # episode = video.episode
        else:
            query = video.title+"."+str(video.year)

        logger.debug('query %r', query)

        return self.query(query)

    def series_video_name_has_year(self, name):
        groups = series_filename_re.match(name)
        if groups.group('year') is None:
            return False
        else:
            return True


    def download_subtitle(self, subtitle):
        logger.info('Downloading archive %s', subtitle)
        r = self.session.get(subtitle.subtitle_url, headers={'Referer': MAIN_SUBDIVX_URL+subtitle.subtitle_id},
                             timeout=10, verify=False)
        r.raise_for_status()

        # open the archive
        content = None
        archive_stream = io.BytesIO(r.content)
        if is_rarfile(archive_stream):
            logger.debug('Identified rar archive')
            content = RarFile(archive_stream)
            # logger.info('RarFile archive %r', content)
        elif is_zipfile(archive_stream):
            logger.debug('Identified zip archive')
            content = ZipFile(archive_stream)

        else:
            raise ValueError('Not a valid archive')

        # TODO
        content_list = content.namelist()
        # NON_LATINO_REFERENCES_IN_FILENAME = ['Espa§a'.decode('utf-8'),'espa§a'.decode('utf-8')]
        NON_LATINO_REFERENCES_IN_FILENAME = ['Espa§a','espa§a']
        # logger.info('archive content_list %r', content_list)

        if len(content_list) == 1:
            sub = fix_line_ending(content.read(content_list[0]))
        else:
            for name in content_list:
                # logger.debug('name archive')
                logger.debug('name archive %s', name)
                # discard thae FORZADOS file
                if name.endswith('FORZADO.srt'):
                    logger.debug('name.endswith(FORZADO.srt): %s', name)
                    continue

                # discard hidden files
                if os.path.split(name)[-1].startswith('.'):
                    logger.debug('os.path.split(name)[-1].startswith(.): %s', name)
                    continue

                    # LatinoamÇrica  Espa§a

                # discard non-subtitle files
                if not name.lower().endswith(MY_SUBTITLE_EXTENSIONS):
                    logger.debug('not name.lower().endswith(SUBTITLE_EXTENSIONS): %s', name)
                    continue
                # discard Espa§a subtitle files
                if any(word in name for word in NON_LATINO_REFERENCES_IN_FILENAME):
                    logger.debug('discard España subtitle files')
                    continue
                else:
                    logger.debug('sub selected: %s', name)
                    sub = fix_line_ending(content.read(name))
        # logger.info('sub %r', sub)
        subtitle.content = sub


# class SubdivxError(ProviderError):
#     """Base class for non-generic :class:`SubdivxProvider` exceptions."""
#     pass
#
#
# class Unauthorized(SubdivxError, AuthenticationError):
#     """Exception raised when status is '401 Unauthorized'."""
#     pass
#
#
# class NoSession(SubdivxError, AuthenticationError):
#     """Exception raised when status is '406 No session'."""
#     pass
#
#
# class DownloadLimitReached(SubdivxError, DownloadLimitExceeded):
#     """Exception raised when status is '407 Download limit reached'."""
#     pass
#
#
# class UnknownUserAgent(SubdivxError, AuthenticationError):
#     """Exception raised when status is '414 Unknown User Agent'."""
#     pass
#
#
# class DisabledUserAgent(SubdivxError, AuthenticationError):
#     """Exception raised when status is '415 Disabled user agent'."""
#     pass
#
#
# class ServiceUnavailable(SubdivxError):
#     """Exception raised when status is '503 Service Unavailable'."""
#     pass
