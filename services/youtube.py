import os
import logging
import datetime
import requests
from typing import List, Dict, Any
import yt_dlp

logger = logging.getLogger(__name__)

class YouTubeService:
    def __init__(self):
        self.api_key = os.getenv("YOUTUBE_API_KEY")
        self.lookback_hours = int(os.getenv("SEARCH_LOOKBACK_HOURS", "24"))
        self.max_videos = int(os.getenv("MAX_VIDEOS_PER_RUN", "15"))
        
        # Specific channels configured by the user
        channels_str = os.getenv("YOUTUBE_CHANNELS", "")
        self.channels = [c.strip() for c in channels_str.split(",") if c.strip()]

    def search_videos(self, keywords: List[str]) -> List[Dict[str, Any]]:
        """
        Searches YouTube for recent videos matching list of keywords
        AND crawls configured specific channels for recent uploads.
        Returns a deduplicated list of video metadata dictionaries.
        """
        all_videos = {}
        threshold_time = datetime.datetime.utcnow() - datetime.timedelta(hours=self.lookback_hours)
        logger.info(f"Searching for videos published after {threshold_time.isoformat()} UTC")

        # 1. Channel specific scanning
        for channel in self.channels:
            logger.info(f"Crawling specific channel: '{channel}'")
            channel_videos = []
            
            if self.api_key:
                try:
                    channel_videos = self._crawl_channel_via_api(channel, threshold_time)
                    logger.info(f"Found {len(channel_videos)} videos for channel '{channel}' via API")
                except Exception as e:
                    logger.error(f"API channel crawl failed for '{channel}', falling back to yt-dlp. Error: {e}")
                    channel_videos = self._crawl_channel_via_ytdlp(channel, threshold_time)
            else:
                channel_videos = self._crawl_channel_via_ytdlp(channel, threshold_time)
                logger.info(f"Found {len(channel_videos)} videos for channel '{channel}' via yt-dlp")
                
            for v in channel_videos:
                if v['video_id'] not in all_videos:
                    all_videos[v['video_id']] = v

        # 2. General Keyword Search (India Focused)
        for keyword in keywords:
            logger.info(f"Searching for keyword: '{keyword}'")
            videos_found = []
            search_limit = 3 # Optimize: only crawl top 3 search results per keyword
            
            if self.api_key:
                try:
                    videos_found = self._search_via_api(keyword, threshold_time, limit=search_limit)
                    logger.info(f"Found {len(videos_found)} videos via API for '{keyword}'")
                except Exception as e:
                    logger.error(f"API search failed for '{keyword}', falling back to yt-dlp. Error: {e}")
                    videos_found = self._search_via_ytdlp(keyword, threshold_time, limit=search_limit)
            else:
                videos_found = self._search_via_ytdlp(keyword, threshold_time, limit=search_limit)
                logger.info(f"Found {len(videos_found)} videos via yt-dlp for '{keyword}'")
            
            for v in videos_found:
                if v['video_id'] not in all_videos:
                    all_videos[v['video_id']] = v

        return list(all_videos.values())

    def _crawl_channel_via_ytdlp(self, channel: str, threshold_time: datetime.datetime) -> List[Dict[str, Any]]:
        """Scrapes channel uploads directly via yt-dlp using channel username or handle."""
        videos = []
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'force_generic_extractor': False,
            'playlistend': self.max_videos,
            'no_warnings': True,
        }
        
        # Normalize handle
        channel_url = channel if channel.startswith("http") else f"https://www.youtube.com/{channel}/videos"
        logger.info(f"Extracting uploads from url: {channel_url}")
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(channel_url, download=False)
                entries = result.get('entries', [])
                
                for entry in entries:
                    if not entry:
                        continue
                        
                    upload_date_str = entry.get('upload_date')
                    if upload_date_str:
                        try:
                            upload_date = datetime.datetime.strptime(upload_date_str, "%Y%m%d")
                        except ValueError:
                            upload_date = datetime.datetime.utcnow()
                    else:
                        upload_date = datetime.datetime.utcnow()
                    
                    if upload_date < threshold_time:
                        continue
                        
                    videos.append({
                        "video_id": entry.get('id'),
                        "title": entry.get('title', ''),
                        "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                        "channel": entry.get('uploader') or channel,
                        "duration": int(entry.get('duration')) if entry.get('duration') else None,
                        "upload_date": upload_date
                    })
        except Exception as e:
            logger.error(f"yt-dlp channel crawl failed for '{channel}': {e}")
            
        return videos

    def _crawl_channel_via_api(self, channel: str, threshold_time: datetime.datetime) -> List[Dict[str, Any]]:
        """Crawls a channel's uploads playlist via the YouTube API."""
        # Clean channel handle
        handle = channel.replace("@", "") if channel.startswith("@") else channel
        
        # 1. Resolve channel username to Channel ID and upload playlist ID
        # Check if handle or ID
        channel_id = None
        if handle.startswith("UC") and len(handle) == 24:
            channel_id = handle
        else:
            # Resolve username/handle
            url = "https://www.googleapis.com/youtube/v3/channels"
            params = {
                "part": "contentDetails",
                "forHandle": handle if channel.startswith("@") else None,
                "forUsername": handle if not channel.startswith("@") else None,
                "key": self.api_key
            }
            if not channel.startswith("@"):
                # handles are usually prefixed with @
                params["forHandle"] = f"@{handle}"
                
            response = requests.get(url, params=params)
            if response.status_code == 200:
                items = response.json().get("items", [])
                if items:
                    channel_id = items[0]["id"]
                    
        if not channel_id:
            raise Exception(f"Could not resolve channel handle '{channel}' to a channel ID.")
            
        # 2. Search videos inside this channel
        search_url = "https://www.googleapis.com/youtube/v3/search"
        published_after = threshold_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "part": "snippet",
            "channelId": channel_id,
            "type": "video",
            "publishedAfter": published_after,
            "maxResults": self.max_videos,
            "key": self.api_key
        }
        
        response = requests.get(search_url, params=params)
        if response.status_code != 200:
            raise Exception(f"YouTube Channel Search API error: {response.text}")
            
        items = response.json().get("items", [])
        if not items:
            return []
            
        video_ids = [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]
        if not video_ids:
            return []
            
        # 3. Fetch details
        details_url = "https://www.googleapis.com/youtube/v3/videos"
        detail_params = {
            "part": "contentDetails,snippet",
            "id": ",".join(video_ids),
            "key": self.api_key
        }
        
        details_response = requests.get(details_url, params=detail_params)
        if details_response.status_code != 200:
            raise Exception(f"YouTube Channel Video Details API error: {details_response.text}")
            
        videos = []
        for item in details_response.json().get("items", []):
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            
            duration_str = content_details.get("duration", "PT0S")
            duration_secs = self._parse_iso_duration(duration_str)
            
            pub_date_str = snippet.get("publishedAt")
            upload_date = datetime.datetime.strptime(pub_date_str, "%Y-%m-%dT%H:%M:%SZ") if pub_date_str else datetime.datetime.utcnow()

            videos.append({
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={item['id']}",
                "channel": snippet.get("channelTitle", ""),
                "duration": duration_secs,
                "upload_date": upload_date
            })
            
        return videos

    def _search_via_api(self, keyword: str, threshold_time: datetime.datetime, limit: int = None) -> List[Dict[str, Any]]:
        """Search using official YouTube API v3."""
        videos = []
        published_after = threshold_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        search_limit = limit or self.max_videos
        
        search_url = "https://www.googleapis.com/youtube/v3/search"
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "publishedAfter": published_after,
            "maxResults": search_limit,
            "key": self.api_key
        }
        
        response = requests.get(search_url, params=params)
        if response.status_code != 200:
            raise Exception(f"YouTube Search API error: {response.text}")
            
        data = response.json()
        items = data.get("items", [])
        if not items:
            return []
            
        video_ids = [item["id"]["videoId"] for item in items if "videoId" in item.get("id", {})]
        if not video_ids:
            return []
            
        details_url = "https://www.googleapis.com/youtube/v3/videos"
        detail_params = {
            "part": "contentDetails,snippet",
            "id": ",".join(video_ids),
            "key": self.api_key
        }
        
        details_response = requests.get(details_url, params=detail_params)
        if details_response.status_code != 200:
            raise Exception(f"YouTube Video Details API error: {details_response.text}")
            
        details_data = details_response.json()
        
        for item in details_data.get("items", []):
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            
            duration_str = content_details.get("duration", "PT0S")
            duration_secs = self._parse_iso_duration(duration_str)
            
            pub_date_str = snippet.get("publishedAt")
            upload_date = datetime.datetime.strptime(pub_date_str, "%Y-%m-%dT%H:%M:%SZ") if pub_date_str else datetime.datetime.utcnow()

            videos.append({
                "video_id": item["id"],
                "title": snippet.get("title", ""),
                "url": f"https://www.youtube.com/watch?v={item['id']}",
                "channel": snippet.get("channelTitle", ""),
                "duration": duration_secs,
                "upload_date": upload_date
            })
            
        return videos

    def _search_via_ytdlp(self, keyword: str, threshold_time: datetime.datetime, limit: int = None) -> List[Dict[str, Any]]:
        """Search using yt-dlp programmatically."""
        videos = []
        search_limit = limit or self.max_videos
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'force_generic_extractor': False,
            'playlistend': search_limit,
            'no_warnings': True,
        }
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                result = ydl.extract_info(f"ytsearch{self.max_videos}:{keyword}", download=False)
                entries = result.get('entries', [])
                
                for entry in entries:
                    if not entry:
                        continue
                        
                    upload_date_str = entry.get('upload_date')
                    if upload_date_str:
                        try:
                            upload_date = datetime.datetime.strptime(upload_date_str, "%Y%m%d")
                        except ValueError:
                            upload_date = datetime.datetime.utcnow()
                    else:
                        upload_date = datetime.datetime.utcnow()
                    
                    if upload_date < threshold_time:
                        continue
                        
                    videos.append({
                        "video_id": entry.get('id'),
                        "title": entry.get('title', ''),
                        "url": f"https://www.youtube.com/watch?v={entry.get('id')}",
                        "channel": entry.get('uploader', ''),
                        "duration": int(entry.get('duration')) if entry.get('duration') else None,
                        "upload_date": upload_date
                    })
        except Exception as e:
            logger.error(f"yt-dlp search execution failed: {e}")
            
        return videos

    def _parse_iso_duration(self, duration_str: str) -> int:
        """Parses ISO 8601 duration (e.g. PT15M33S) into seconds."""
        import re
        pattern = re.compile(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?')
        match = pattern.match(duration_str)
        if not match:
            return 0
            
        hours = int(match.group(1)) if match.group(1) else 0
        minutes = int(match.group(2)) if match.group(2) else 0
        seconds = int(match.group(3)) if match.group(3) else 0
        
        return hours * 3600 + minutes * 60 + seconds
