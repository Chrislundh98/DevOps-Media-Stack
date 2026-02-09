import requests
import logging
import os
from datetime import datetime


class DiscordNotifier:

    def __init__(self, webhook_url=None, hook_type='torrent'):
        if webhook_url:
            self.webhook_url = webhook_url
        elif hook_type == 'stats':
            self.webhook_url = os.getenv('DISCORD_STATS_HOOK')
        else:
            self.webhook_url = os.getenv('DISCORD_TORRENT_HOOK')

    def send_message(self, content, title=None, color=None, fields=None):
        if not self.webhook_url:
            logging.warning("Discord webhook URL not configured")
            return False

        if title:
            embed = {
                'title': title,
                'description': content,
                'color': color or 3447003,
                'timestamp': datetime.utcnow().isoformat(),
            }

            if fields:
                embed['fields'] = fields

            payload = {'embeds': [embed]}
        else:
            payload = {'content': content}

        try:
            response = requests.post(self.webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            return True
        except Exception as e:
            logging.error(f"Failed to send Discord notification: {e}")
            return False

    def send_removal_report(self, removed_list, failed_list=None, tracker_name="Tracker"):
        if not removed_list and not failed_list:
            return

        total_removed = len(removed_list)
        total_failed = len(failed_list) if failed_list else 0
        total_size_mb = sum(t.get('size_mb', 0) for t in removed_list)
        total_size_gb = total_size_mb / 1024

        title = f"🗑️ {tracker_name} Cleanup Report"

        fields = [
            {
                'name': '📊 Summary',
                'value': f"Removed: {total_removed}\nFailed: {total_failed}\nFreed: {total_size_gb:.2f} GB",
                'inline': False
            }
        ]

        if removed_list:
            removed_text = '\n'.join([
                f"• {t['name'][:50]}... ({t.get('size_mb', 0) / 1024:.2f} GB)"
                for t in removed_list[:10]
            ])
            if total_removed > 10:
                removed_text += f"\n... and {total_removed - 10} more"

            fields.append({
                'name': '✅ Removed Torrents',
                'value': removed_text,
                'inline': False
            })

        if failed_list:
            failed_text = '\n'.join([
                f"• {t['name'][:50]}..."
                for t in failed_list[:5]
            ])
            if total_failed > 5:
                failed_text += f"\n... and {total_failed - 5} more"

            fields.append({
                'name': '❌ Failed to Match',
                'value': failed_text,
                'inline': False
            })

        color = 3066993 if total_removed > 0 else 15158332

        self.send_message(
            content=f"Cleaned up {total_removed} torrents, freed {total_size_gb:.2f} GB",
            title=title,
            color=color,
            fields=fields
        )

    def send_stats_report(self, stats_data, tracker_name="Tracker"):
        title = f"📈 {tracker_name} Stats Report"

        fields = []
        for key, value in stats_data.items():
            if isinstance(value, dict):
                field_value = '\n'.join([f"{k}: {v}" for k, v in value.items()])
            else:
                field_value = str(value)

            fields.append({
                'name': key,
                'value': field_value,
                'inline': True
            })

        self.send_message(
            content="Daily statistics report",
            title=title,
            color=5793266,
            fields=fields
        )

    def send_error(self, error_message, context=None):
        title = "⚠️ Error Alert"
        content = error_message

        if context:
            content = f"{error_message}\n\n**Context:** {context}"

        self.send_message(content, title=title, color=15158332)
