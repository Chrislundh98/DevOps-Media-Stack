#!/usr/bin/env python3

"""
Tracker Identification Utilities
Identifies which tracker/indexer a torrent belongs to by checking its tracker URL.
"""

import logging

class TrackerIdentifier:
    """Utility class for identifying torrent trackers"""
    
    # Define tracker domains for each indexer
    TORRENTLEECH_DOMAINS = [
        'tracker.tleechreload.org',
        'tracker.torrentleech.org'
    ]
    
    DIGITALCORE_DOMAINS = [
        'tracker.digitalcore.club',
        'trackerprxy.digitalcore.club'
    ]
    
    @staticmethod
    def get_torrent_tracker(qbt_client, torrent_hash):
        """
        Get the primary tracker URL for a torrent.
        
        Args:
            qbt_client: qBittorrent API client instance
            torrent_hash: Hash of the torrent
            
        Returns:
            str: Tracker URL or None if no valid tracker found
        """
        try:
            trackers = qbt_client.torrents_trackers(torrent_hash)
            
            # Filter out DHT, PeX, LSD, and empty "trackers"
            real_trackers = [
                t for t in trackers 
                if t.url and t.url.startswith('http')
            ]
            
            if real_trackers:
                return real_trackers[0].url
            return None
            
        except Exception as e:
            logging.warning(f"Failed to get tracker for hash {torrent_hash}: {e}")
            return None
    
    @staticmethod
    def is_torrentleech_torrent(tracker_url):
        """
        Check if tracker URL belongs to TorrentLeech.
        
        Args:
            tracker_url: Full tracker URL string
            
        Returns:
            bool: True if TorrentLeech tracker, False otherwise
        """
        if not tracker_url:
            return False
        return any(domain in tracker_url for domain in TrackerIdentifier.TORRENTLEECH_DOMAINS)
    
    @staticmethod
    def is_digitalcore_torrent(tracker_url):
        """
        Check if tracker URL belongs to DigitalCore.
        
        Args:
            tracker_url: Full tracker URL string
            
        Returns:
            bool: True if DigitalCore tracker, False otherwise
        """
        if not tracker_url:
            return False
        return any(domain in tracker_url for domain in TrackerIdentifier.DIGITALCORE_DOMAINS)
    
    @staticmethod
    def identify_tracker(tracker_url):
        """
        Identify which tracker/indexer a URL belongs to.
        
        Args:
            tracker_url: Full tracker URL string
            
        Returns:
            str: Tracker name ('TorrentLeech', 'DigitalCore', or 'Unknown')
        """
        if TrackerIdentifier.is_torrentleech_torrent(tracker_url):
            return 'TorrentLeech'
        elif TrackerIdentifier.is_digitalcore_torrent(tracker_url):
            return 'DigitalCore'
        else:
            return 'Unknown'
    
    @staticmethod
    def filter_torrents_by_tracker(qbt_client, torrents, tracker_name):
        """
        Filter a list of torrents to only include those from a specific tracker.
        
        Args:
            qbt_client: qBittorrent API client instance
            torrents: List of torrent objects from qBittorrent
            tracker_name: Name of tracker to filter for ('TorrentLeech' or 'DigitalCore')
            
        Returns:
            list: Filtered list of torrents from the specified tracker
        """
        filtered = []
        
        for torrent in torrents:
            tracker_url = TrackerIdentifier.get_torrent_tracker(qbt_client, torrent.hash)
            identified = TrackerIdentifier.identify_tracker(tracker_url)
            
            if identified == tracker_name:
                filtered.append(torrent)
        
        return filtered
    
    @staticmethod
    def get_tracker_statistics(qbt_client, torrents):
        """
        Get statistics about which trackers are being used.
        
        Args:
            qbt_client: qBittorrent API client instance
            torrents: List of torrent objects from qBittorrent
            
        Returns:
            dict: Statistics with counts per tracker
        """
        stats = {
            'TorrentLeech': 0,
            'DigitalCore': 0,
            'Unknown': 0
        }
        
        for torrent in torrents:
            tracker_url = TrackerIdentifier.get_torrent_tracker(qbt_client, torrent.hash)
            identified = TrackerIdentifier.identify_tracker(tracker_url)
            stats[identified] = stats.get(identified, 0) + 1
        
        return stats