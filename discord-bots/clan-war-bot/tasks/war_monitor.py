import asyncio
import discord
import random
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING
from discord.ext import tasks

import config
import database as db
from coc_api import CoCAPI, CoCAPIError, parse_coc_timestamp, format_time_remaining

if TYPE_CHECKING:
    from discord.ext.commands import Bot

# Reminder thresholds in hours
REMINDER_THRESHOLDS = [8, 4, 2, 1]  # 8h, 4h, 2h, 1h

# Random reminder messages for non-cleared wars
# {mentions} = @user1 @user2, {time} = "4 hours", {accounts} = account info if multi-account
REMINDER_MESSAGES_ATTACK = [
    "{mentions} Wake up slacker, {time} left. Go clear a base or something.",
    "{mentions} {time} remaining and you're still sleeping? Pathetic.",
    "{mentions} Clock's ticking. {time} left. Move it.",
    "{mentions} Hey genius, {time} left and your attacks aren't gonna use themselves.",
    "{mentions} {time} left. I believe in you. Actually no I don't. Just attack.",
    "{mentions} War ends in {time}. Your troops are getting impatient.",
    "{mentions} {time} remaining. Did you forget how to play this game?",
    "{mentions} Friendly reminder that {time} left and you're being useless.",
    "{mentions} {time} left. The enemy bases aren't gonna three-star themselves.",
    "{mentions} Rise and shine, {time} left to prove you're not dead weight.",
    "{mentions} {time} remaining. Your clan needs you. Unfortunately.",
    "{mentions} {time} left. Attack now or forever hold your peace. Actually, just attack.",
    "{mentions} Hey, {time} left. Remember when you said you'd attack early? Good times.",
    "{mentions} {time} remaining and still no attacks? Bold strategy.",
    "{mentions} The war ends in {time}. Your excuses won't save you.",
    "{mentions} {time} left. Even my grandmother attacks faster than you.",
    "{mentions} Tick tock, {time} left. Stop scrolling Discord and go attack.",
    "{mentions} {time} remaining. I'm not mad, just disappointed. Okay, I'm mad too.",
    "{mentions} {time} left to attack. Don't make me ping you again.",
    "{mentions} {time} remaining. The clan is watching. And judging.",
    "{mentions} Hey, {time} left. Your base isn't going to revenge itself.",
    "{mentions} {time} left and counting. What's your excuse this time?",
    "{mentions} War ends in {time}. Get off your phone and onto the battlefield.",
    "{mentions} {time} remaining. I swear if you miss this attack...",
    "{mentions} {time} left. The troops are loaded. The spells are ready. WHERE ARE YOU?",
    "{mentions} Reminder #{random}: {time} left. Yes I'm counting.",
    "{mentions} {time} left. Do it for the ores. Do it for glory. Just do it.",
    "{mentions} {time} remaining. This message will self-destruct. Your attacks won't though, because you haven't used them.",
    "{mentions} {time} left. I've seen faster attacks from a TH3.",
    "{mentions} {time} remaining. Attack or I'm telling the co-leaders.",
]

# Random reminder messages for 100% cleared wars (ore collection)
REMINDER_MESSAGES_ORES = [
    "{mentions} War is 100% cleared but you still have attacks. Free ores, people.",
    "{mentions} Do you even want your ores? War's cleared, go collect.",
    "{mentions} 100% cleared. Your attacks are just free ores now. You're welcome.",
    "{mentions} War's won, but you're leaving ores on the table. Rookie mistake.",
    "{mentions} The war's done but your ores aren't collected. Priorities?",
    "{mentions} Free ores sitting there and you can't be bothered? Unbelievable.",
    "{mentions} War cleared. Ores available. You: nowhere to be found.",
    "{mentions} 100% destruction achieved. Now go get your participation trophy (ores).",
    "{mentions} We won without you. Least you can do is grab your ores.",
    "{mentions} Ores are waiting. We already did the hard part.",
    "{mentions} War's cleared. Your ores are gathering dust.",
    "{mentions} Go collect your ores before I give them to someone who cares.",
    "{mentions} 100% cleared. Attack for ores or don't. See if I care. (I don't.)",
    "{mentions} Free ores available. This isn't complicated.",
    "{mentions} War done, ores unclaimed. This is why we can't have nice things.",
]

class WarMonitor:
    def __init__(self, bot: "Bot", coc_api: CoCAPI):
        self.bot = bot
        self.coc = coc_api
        self.last_state = None
        self.last_war_id = None
        self.is_cwl = False
    
    def generate_war_id(self, war_data: dict) -> str:
        """Generate a unique ID for a war based on start time and opponent"""
        start_time = war_data.get("startTime", "")
        opponent = war_data.get("opponent", {}).get("tag", "")
        return f"{start_time}_{opponent}"
    
    async def get_reminder_channel(self) -> Optional[discord.TextChannel]:
        """Get the reminder channel"""
        channel_id = config.REMINDER_CHANNEL_ID
        if not channel_id:
            print("[WarMonitor] No REMINDER_CHANNEL_ID configured")
            return None
        
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            print(f"[WarMonitor] Could not find channel {channel_id}")
            return None
        
        return channel
    
    async def get_coleader_channel(self) -> Optional[discord.TextChannel]:
        """Get the co-leader channel for private messages"""
        channel_id = config.COLEADER_CHANNEL_ID
        if not channel_id:
            return None
        
        channel = self.bot.get_channel(int(channel_id))
        if not channel:
            print(f"[WarMonitor] Could not find co-leader channel {channel_id}")
            return None
        
        return channel
    
    def generate_cwl_season_id(self, league_group: dict) -> str:
        """Generate a unique ID for a CWL season"""
        # Use the season identifier from the league group
        season = league_group.get("season", "")
        clan_tag = config.CLAN_TAG.upper().replace("#", "")
        return f"cwl_{season}_{clan_tag}"
    
    async def get_cwl_group_data(self) -> Optional[dict]:
        """Get the CWL league group data"""
        try:
            return await self.coc.get_cwl_group(config.CLAN_TAG)
        except CoCAPIError:
            return None
    
    def get_cwl_day_number(self, league_group: dict, war_data: dict) -> int:
        """Determine which day of CWL this war is"""
        rounds = league_group.get("rounds", [])
        war_start = war_data.get("startTime", "")

        for i, round_data in enumerate(rounds):
            war_tags = round_data.get("warTags", [])
            for war_tag in war_tags:
                # Since we have the war_data, match by startTime
                pass

        # Fallback: count by start time ordering
        # Each CWL day is roughly 24 hours apart
        return len([r for r in rounds if r.get("warTags", ["#0"])[0] != "#0"])

    async def get_cwl_day_war_data(self, league_group: dict, day_number: int) -> Optional[dict]:
        """Fetch our clan's war data for a specific CWL day (1-indexed)."""
        if day_number < 1:
            return None

        rounds = league_group.get("rounds", [])
        if day_number > len(rounds):
            return None

        round_data = rounds[day_number - 1]
        war_tags = round_data.get("warTags", [])

        our_tag = config.CLAN_TAG.upper()
        if not our_tag.startswith("#"):
            our_tag = f"#{our_tag}"

        for war_tag in war_tags:
            if war_tag == "#0":
                continue
            try:
                war_data = await self.coc.get_cwl_war(war_tag)
                clan = war_data.get("clan", {})
                opponent = war_data.get("opponent", {})
                if clan.get("tag", "").upper() == our_tag or opponent.get("tag", "").upper() == our_tag:
                    if opponent.get("tag", "").upper() == our_tag:
                        war_data["clan"], war_data["opponent"] = war_data["opponent"], war_data["clan"]
                    return war_data
            except CoCAPIError:
                continue

        return None

    def record_cwl_war_data(self, war_data: dict, cwl_day: int, season_id: str):
        """Persist CWL day performance to the database. Idempotent."""
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        clan_members = clan.get("members", [])
        opponent_tag = opponent.get("tag", "")

        for member in clan_members:
            attacks = member.get("attacks", [])
            stars = sum(a.get("stars", 0) for a in attacks)
            destruction = sum(a.get("destructionPercentage", 0) for a in attacks)
            attack_used = 1 if attacks else 0

            db.record_cwl_performance(
                season_id=season_id,
                day_number=cwl_day,
                player_tag=member.get("tag"),
                player_name=member.get("name"),
                stars=stars,
                destruction=destruction,
                attack_used=attack_used,
                in_roster=1,
                opponent_tag=opponent_tag
            )

        db.update_cwl_day(season_id, cwl_day)
    
    async def get_current_war(self) -> tuple[Optional[dict], bool]:
        """
        Get current war data. Returns (war_data, is_cwl).
        Tries regular war first, then CWL.
        """
        regular_war_data = None
        
        # Try regular war first
        try:
            war_data = await self.coc.get_current_war(config.CLAN_TAG)
            state = war_data.get("state")
            
            # If in active regular war, return it
            if state in ["preparation", "inWar"]:
                return war_data, False
            
            # Store ended war in case we need it later
            if state == "warEnded":
                regular_war_data = war_data
                
        except CoCAPIError as e:
            # 403 = private war log (common during CWL)
            # 404 = not found
            if e.status not in [403, 404]:
                print(f"[WarMonitor] Error fetching regular war: {e}")
        
        # Try CWL - this takes priority if there's an active CWL war
        try:
            cwl_data = await self.get_current_cwl_war()
            if cwl_data:
                state = cwl_data.get("state")
                # Return CWL war if active OR just ended (for end announcements)
                if state in ["preparation", "inWar", "warEnded"]:
                    return cwl_data, True
        except CoCAPIError as e:
            if e.status != 404:
                print(f"[WarMonitor] Error fetching CWL: {e}")
        
        # No active war found, return ended regular war if exists (for end announcements)
        if regular_war_data:
            return regular_war_data, False
        
        return None, False
    
    async def get_current_cwl_war(self) -> Optional[dict]:
        """Get the current active CWL war if any, or an unannounced ended war"""
        try:
            league_group = await self.coc.get_cwl_group(config.CLAN_TAG)
        except CoCAPIError:
            return None
        
        # Find our clan in the group
        our_tag = config.CLAN_TAG.upper()
        if not our_tag.startswith("#"):
            our_tag = f"#{our_tag}"
        
        # Get all rounds
        rounds = league_group.get("rounds", [])
        
        # Track best active war and all unannounced ended wars
        best_active_war = None
        best_active_priority = -1  # inWar=2, preparation=1
        unannounced_ended_wars = []

        for round_data in rounds:
            war_tags = round_data.get("warTags", [])

            for war_tag in war_tags:
                if war_tag == "#0":  # Placeholder for wars not yet started
                    continue

                try:
                    war_data = await self.coc.get_cwl_war(war_tag)

                    # Check if our clan is in this war
                    clan = war_data.get("clan", {})
                    opponent = war_data.get("opponent", {})

                    if clan.get("tag", "").upper() == our_tag or opponent.get("tag", "").upper() == our_tag:
                        # Swap if we're the opponent
                        if opponent.get("tag", "").upper() == our_tag:
                            war_data["clan"], war_data["opponent"] = war_data["opponent"], war_data["clan"]

                        state = war_data.get("state")

                        if state == "inWar":
                            # Active war takes highest priority, return immediately
                            return war_data
                        elif state == "preparation":
                            if best_active_priority < 1:
                                best_active_war = war_data
                                best_active_priority = 1
                        elif state == "warEnded":
                            war_id = self.generate_war_id(war_data)
                            if not db.has_war_end_been_announced(war_id):
                                unannounced_ended_wars.append(war_data)

                except CoCAPIError:
                    continue

        # If multiple unannounced ended wars exist (e.g. after a bot restart that
        # cleared DB state), only announce the most recent one and silently mark
        # the older ones as announced so they don't flood the channel.
        if unannounced_ended_wars:
            unannounced_ended_wars.sort(key=lambda w: w.get("endTime", ""))
            for stale in unannounced_ended_wars[:-1]:
                stale_id = self.generate_war_id(stale)
                db.mark_war_end_announced(stale_id)
                print(f"[WarMonitor] Silently marked stale CWL war as announced: {stale_id}")
            return unannounced_ended_wars[-1]

        return best_active_war
    
    def get_members_with_remaining_attacks(self, war_data: dict, is_cwl: bool) -> list[dict]:
        """
        Get list of members who haven't used all their attacks.
        Returns list of dicts with: tag, name, attacks_used, attacks_total
        """
        clan_members = war_data.get("clan", {}).get("members", [])
        attacks_per_member = 1 if is_cwl else 2
        
        members_remaining = []
        
        for member in clan_members:
            attacks_used = len(member.get("attacks", []))
            
            if attacks_used < attacks_per_member:
                members_remaining.append({
                    "tag": member.get("tag"),
                    "name": member.get("name"),
                    "attacks_used": attacks_used,
                    "attacks_total": attacks_per_member,
                    "attacks_remaining": attacks_per_member - attacks_used
                })
        
        return members_remaining
    
    async def send_message(self, content: str = None, embed: discord.Embed = None, channel: discord.TextChannel = None):
        """Send a message to a channel (defaults to reminder channel)"""
        if channel is None:
            channel = await self.get_reminder_channel()
        if not channel:
            return
        
        try:
            await channel.send(content=content, embed=embed)
        except Exception as e:
            print(f"[WarMonitor] Failed to send message: {e}")
    
    async def send_war_ended_announcement(self, war_data: dict, is_cwl: bool):
        """Send announcement when war ends with summary (no pings)"""
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        
        clan_stars = clan.get("stars", 0)
        opponent_stars = opponent.get("stars", 0)
        clan_destruction = clan.get("destructionPercentage", 0)
        opponent_destruction = opponent.get("destructionPercentage", 0)
        team_size = war_data.get("teamSize", 0)
        
        # Calculate attack stats
        clan_members = clan.get("members", [])
        total_attacks_used = sum(len(m.get("attacks", [])) for m in clan_members)
        max_attacks = team_size * (1 if is_cwl else 2)
        
        # Determine result
        if clan_stars > opponent_stars:
            result = "Victory"
            color = discord.Color.green()
        elif clan_stars < opponent_stars:
            result = "Defeat"
            color = discord.Color.red()
        else:
            if clan_destruction > opponent_destruction:
                result = "Victory (by destruction)"
                color = discord.Color.green()
            elif clan_destruction < opponent_destruction:
                result = "Defeat (by destruction)"
                color = discord.Color.red()
            else:
                result = "Draw"
                color = discord.Color.gold()
        
        war_type = "CWL" if is_cwl else "Clan War"
        
        # Find members who didn't use all attacks
        members_remaining = self.get_members_with_remaining_attacks(war_data, is_cwl)
        
        embed = discord.Embed(
            title=f"{war_type} Ended - {result}",
            description=f"**{clan.get('name')}** vs **{opponent.get('name')}**",
            color=color
        )
        
        # Our stats
        embed.add_field(
            name=clan.get('name'),
            value=f"⭐ {clan_stars} stars\n{clan_destruction:.1f}% destruction",
            inline=True
        )
        
        # Opponent stats
        embed.add_field(
            name=opponent.get('name'),
            value=f"⭐ {opponent_stars} stars\n{opponent_destruction:.1f}% destruction",
            inline=True
        )
        
        # War summary
        star_diff = clan_stars - opponent_stars
        dest_diff = clan_destruction - opponent_destruction
        summary = f"Attacks used: {total_attacks_used}/{max_attacks}"
        if star_diff > 0:
            summary += f"\nWon by {star_diff} star{'s' if star_diff != 1 else ''}"
        elif star_diff < 0:
            summary += f"\nLost by {abs(star_diff)} star{'s' if abs(star_diff) != 1 else ''}"
        else:
            summary += f"\nDestruction difference: {dest_diff:+.1f}%"
        
        embed.add_field(name="Summary", value=summary, inline=False)
        
        if members_remaining:
            missed_names = [f"{m['name']} ({m['attacks_remaining']} unused)" for m in members_remaining]
            missed_text = ", ".join(missed_names[:15])
            if len(missed_names) > 15:
                missed_text += f" +{len(missed_names) - 15} more"
            embed.add_field(name="Missed Attacks", value=missed_text, inline=False)
        
        await self.send_message(embed=embed)
    
    async def send_attack_reminder(self, war_data: dict, is_cwl: bool, hours_remaining: float):
        """Send reminder pinging users who haven't attacked"""
        members_remaining = self.get_members_with_remaining_attacks(war_data, is_cwl)
        
        if not members_remaining:
            return
        
        # Check if war is 100% cleared
        clan = war_data.get("clan", {})
        clan_destruction = clan.get("destructionPercentage", 0)
        is_fully_cleared = clan_destruction >= 100.0
        
        # If 100% cleared, only send reminder at 1 hour mark
        if is_fully_cleared and hours_remaining > 1:
            return
        
        war_id = self.generate_war_id(war_data)
        
        # Format time string
        if hours_remaining >= 1:
            time_str = f"{int(hours_remaining)} hour{'s' if hours_remaining != 1 else ''}"
        else:
            minutes = int(hours_remaining * 60)
            time_str = f"{minutes} minutes"
        
        # Build ping list - group by Discord user
        # Structure: {discord_id: [list of account names that need attacks]}
        discord_user_accounts = {}
        unlinked_accounts = []
        
        for member in members_remaining:
            player_tag = member["tag"]
            player_name = member["name"]
            
            # Check if reminder already sent
            threshold_key = int(hours_remaining * 60)
            if db.has_reminder_been_sent(war_id, player_tag, threshold_key):
                continue
            
            # Get linked Discord users
            discord_ids = db.get_discord_ids_for_account(player_tag)
            
            if discord_ids:
                for did in discord_ids:
                    if did not in discord_user_accounts:
                        discord_user_accounts[did] = []
                    discord_user_accounts[did].append(player_name)
            else:
                unlinked_accounts.append({
                    "name": player_name,
                    "tag": player_tag
                })
            
            # Record that we sent this reminder
            db.record_reminder_sent(war_id, player_tag, threshold_key)
        
        # Nothing new to send
        if not discord_user_accounts and not unlinked_accounts:
            return
        
        # Build message for linked users
        if discord_user_accounts:
            # Check if any user has multiple accounts that need attacks
            # to decide whether to show account names
            user_needs_account_info = {}
            for did, accounts in discord_user_accounts.items():
                # Get total accounts linked to this user
                all_user_accounts = db.get_accounts_for_discord_user(did)
                # If user has multiple accounts total, show which ones need attacks
                user_needs_account_info[did] = len(all_user_accounts) > 1
            
            # Build mention text with optional account names
            mention_parts = []
            for did, accounts in discord_user_accounts.items():
                mention = f"<@{did}>"
                if user_needs_account_info[did]:
                    # Show which accounts need attacks
                    account_names = ", ".join(accounts)
                    mention_parts.append(f"{mention} ({account_names})")
                else:
                    mention_parts.append(mention)
            
            mentions_text = " ".join(mention_parts)
            
            # Pick random message
            if is_fully_cleared:
                message_template = random.choice(REMINDER_MESSAGES_ORES)
            else:
                message_template = random.choice(REMINDER_MESSAGES_ATTACK)
            
            # Format message
            message = message_template.format(
                mentions=mentions_text,
                time=time_str,
                random=random.randint(1, 999)
            )
            
            # Add note about unlinked accounts (if any and not 100% cleared)
            if unlinked_accounts and not is_fully_cleared:
                unlinked_names = ", ".join(u['name'] for u in unlinked_accounts)
                message += f"\n*(Also couldn't find a Discord match for: {unlinked_names})*"
            
            await self.send_message(content=message)
        
        # If ONLY unlinked accounts (no linked users to ping), send standalone message at all thresholds
        elif unlinked_accounts and not is_fully_cleared:
            names = ", ".join(u['name'] for u in unlinked_accounts)
            
            if hours_remaining <= 1:
                # Final warning - ping Co-leaders
                ping_text = ""
                if self.bot.guilds:
                    guild = self.bot.guilds[0]
                    for role in guild.roles:
                        if role.name.lower() == "co-leader":
                            ping_text = f"<@&{role.id}> "
                            break
                await self.send_message(content=f"{ping_text}No Discord match for {names}, contact manually!")
            else:
                # Earlier reminders - just note it without pinging co-leaders
                await self.send_message(content=f"*Couldn't find Discord match for: {names} ({time_str} left)*")
        
        # Co-leader ping for unlinked at 1hr (even if there were linked users above)
        if unlinked_accounts and hours_remaining <= 1 and not is_fully_cleared and discord_user_accounts:
            names = ", ".join(u['name'] for u in unlinked_accounts)
            
            # Find Co-leader role by name
            ping_text = ""
            if self.bot.guilds:
                guild = self.bot.guilds[0]
                for role in guild.roles:
                    if role.name.lower() == "co-leader":
                        ping_text = f"<@&{role.id}>"
                        break
            
            await self.send_message(content=f"{ping_text} no Discord match for {names}, contact manually")
    
    async def send_war_started_announcement(self, war_data: dict, is_cwl: bool, cwl_day: int = None,
                                             prev_day_war_data: dict = None, season_id: str = None):
        """Send announcement when battle day starts.

        For CWL day 2+ when prev_day_war_data is provided, the previous day's
        result is bundled into the embed.
        """
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        end_time = parse_coc_timestamp(war_data.get("endTime"))

        if is_cwl and cwl_day:
            title = f"CWL Day {cwl_day} has begun"
        else:
            war_type = "CWL" if is_cwl else "War"
            title = f"{war_type} has begun"
        
        _WAR_BEGUN_QUIPS = [
            "War has started. You know the deal — 3 ⭐ or 👢",
            "New day, new opponents, same rules. 3 ⭐ or 👢",
            "Swords out, lads. 3 ⭐ or 👢",
            "Rise and grind. 3 ⭐ or 👢",
            "No excuses today. 3 ⭐ or 👢",
            "The war horn sounds. 3 ⭐ or 👢",
            "Another war, another chance to prove yourself. 3 ⭐ or 👢",
            "We attack with love. But also — 3 ⭐ or 👢",
            "Friendly reminder from clan leadership: 3 ⭐ or 👢",
            "Our opponents don't know what's coming. 3 ⭐ or 👢",
            "Time to earn your keep. 3 ⭐ or 👢",
            "The boot has been polished. 3 ⭐ or 👢",
            "Let's make it clean. 3 ⭐ or 👢",
            "You've got this. Probably. 3 ⭐ or 👢",
            "War started. Clock's ticking. 3 ⭐ or 👢",
            "New war dropped. You know the assignment — 3 ⭐ or 👢",
            "Respect the tradition. 3 ⭐ or 👢",
            "Don't overthink it. 3 ⭐ or 👢",
            "Good luck out there. You'll need it. 3 ⭐ or 👢",
            "The clan is watching. 3 ⭐ or 👢",
            "Smash 'em into the ground. 3 ⭐ or 👢",
            "Battle day is here. No pressure, but — 3 ⭐ or 👢",
            "This is your sign to not mess up. 3 ⭐ or 👢",
            "War has begun. The math is simple: 3 ⭐ or 👢",
            "Another war, another shot at glory. 3 ⭐ or 👢",
            "The opponents picked the wrong clan. 3 ⭐ or 👢 — let's show them.",
            "May your fingers be fast and your stars be three. 3 ⭐ or 👢",
            "Every attack counts. 3 ⭐ or 👢",
            "We believe in you. The boot believes in you too. 3 ⭐ or 👢",
            "War on. You know what to do. 3 ⭐ or 👢",
        ]

        quip = random.choice(_WAR_BEGUN_QUIPS)
        embed = discord.Embed(
            title=title,
            description=f"**{clan.get('name')}** vs **{opponent.get('name')}**\nEnds in {format_time_remaining(end_time)}\n\n{quip}",
            color=discord.Color.purple()
        )

        # Bundle previous CWL day's result into this announcement (Day 2+)
        if is_cwl and cwl_day and cwl_day > 1 and prev_day_war_data:
            prev_field = self._format_prev_cwl_day_field(prev_day_war_data, cwl_day - 1, season_id)
            if prev_field:
                name, value = prev_field
                embed.add_field(name=name, value=value, inline=False)

        await self.send_message(embed=embed)

    def _format_prev_cwl_day_field(self, prev_war: dict, prev_day: int, season_id: str = None):
        """Build the (name, value) tuple summarising the previous CWL day."""
        prev_clan = prev_war.get("clan", {})
        prev_opp = prev_war.get("opponent", {})

        clan_stars = prev_clan.get("stars", 0)
        opp_stars = prev_opp.get("stars", 0)
        clan_dest = prev_clan.get("destructionPercentage", 0)
        opp_dest = prev_opp.get("destructionPercentage", 0)

        if clan_stars > opp_stars:
            result = "Victory"
        elif clan_stars < opp_stars:
            result = "Defeat"
        else:
            if clan_dest > opp_dest:
                result = "Victory (tiebreaker)"
            elif clan_dest < opp_dest:
                result = "Defeat (tiebreaker)"
            else:
                result = "Draw"

        prev_members = prev_clan.get("members", [])
        team_size = prev_war.get("teamSize", len(prev_members))
        attacks_used = sum(len(m.get("attacks", [])) for m in prev_members)
        missed = [m.get("name") for m in prev_members if not m.get("attacks")]

        lines = [
            f"**{result}** vs {prev_opp.get('name', '?')}",
            f"{clan_stars}⭐ ({clan_dest:.0f}%) — {opp_stars}⭐ ({opp_dest:.0f}%)",
            f"Attacks: {attacks_used}/{team_size}",
        ]
        if missed:
            missed_text = ", ".join(missed[:6])
            if len(missed) > 6:
                missed_text += f" +{len(missed) - 6} more"
            lines.append(f"Missed: {missed_text}")

        if season_id:
            try:
                season_stats = db.get_cwl_season_performance(season_id)
                total_stars = sum(p.get("total_stars", 0) for p in season_stats)
                total_attacks = sum(p.get("attacks_used", 0) for p in season_stats)
                total_possible = prev_day * team_size
                if total_possible > 0:
                    lines.append("")
                    lines.append(f"**Season:** {total_stars}⭐ | {total_attacks}/{total_possible} attacks")
            except Exception as e:
                print(f"[WarMonitor] Could not fetch season totals: {e}")

        return (f"📊 Day {prev_day} Result", "\n".join(lines))
    
    async def send_cwl_war_ended_announcement(self, war_data: dict, cwl_day: int, season_id: str):
        """Send CWL war ended announcement with daily and running totals"""
        clan = war_data.get("clan", {})
        opponent = war_data.get("opponent", {})
        
        clan_stars = clan.get("stars", 0)
        opponent_stars = opponent.get("stars", 0)
        clan_destruction = clan.get("destructionPercentage", 0)
        opponent_destruction = opponent.get("destructionPercentage", 0)
        team_size = war_data.get("teamSize", 0)
        
        # Calculate attack stats
        clan_members = clan.get("members", [])
        total_attacks_used = sum(len(m.get("attacks", [])) for m in clan_members)
        max_attacks = team_size  # CWL is 1 attack per member
        
        # Determine result
        if clan_stars > opponent_stars:
            result = "Victory"
            color = discord.Color.green()
        elif clan_stars < opponent_stars:
            result = "Defeat"
            color = discord.Color.red()
        else:
            if clan_destruction > opponent_destruction:
                result = "Victory (tiebreaker)"
                color = discord.Color.green()
            elif clan_destruction < opponent_destruction:
                result = "Defeat (tiebreaker)"
                color = discord.Color.red()
            else:
                result = "Draw"
                color = discord.Color.gold()
        
        # Persist day stats (idempotent — safe to call again if already recorded)
        self.record_cwl_war_data(war_data, cwl_day, season_id)

        # Get running totals
        season_stats = db.get_cwl_season_performance(season_id)
        total_season_stars = sum(p.get("total_stars", 0) for p in season_stats)
        total_attacks = sum(p.get("attacks_used", 0) for p in season_stats)
        total_possible = cwl_day * team_size
        
        # Build embed
        embed = discord.Embed(
            title=f"CWL Day {cwl_day} Ended - {result}",
            color=color
        )
        
        embed.add_field(
            name=clan.get("name"),
            value=f"{clan_stars} stars | {clan_destruction:.1f}%",
            inline=True
        )
        embed.add_field(
            name=opponent.get("name"),
            value=f"{opponent_stars} stars | {opponent_destruction:.1f}%",
            inline=True
        )
        
        # Today's summary
        star_diff = clan_stars - opponent_stars
        if star_diff > 0:
            summary = f"Won by {star_diff} stars"
        elif star_diff < 0:
            summary = f"Lost by {abs(star_diff)} stars"
        else:
            dest_diff = clan_destruction - opponent_destruction
            summary = f"Tied on stars, destruction: {dest_diff:+.1f}%"
        
        embed.add_field(
            name="Today",
            value=f"Attacks: {total_attacks_used}/{max_attacks}\n{summary}",
            inline=False
        )
        
        # Running total
        embed.add_field(
            name=f"CWL Total (Day {cwl_day}/7)",
            value=f"Stars: {total_season_stars} | Attacks: {total_attacks}/{total_possible}",
            inline=False
        )
        
        # Top performers today (only show if meaningful differentiation)
        today_perf = db.get_cwl_day_performance(season_id, cwl_day)
        top_today = [p for p in today_perf if p.get("attack_used") == 1][:3]
        if top_today and len(set(p.get("stars") for p in top_today)) > 1:  # Only if different star counts
            top_lines = [f"{p['player_name']}: {p['stars']}★ {p['destruction']:.0f}%" for p in top_today]
            embed.add_field(
                name="Top Attacks Today",
                value="\n".join(top_lines),
                inline=False
            )
        
        # Missed attacks
        missed = [m.get("name") for m in clan_members if len(m.get("attacks", [])) == 0]
        if missed:
            missed_text = ", ".join(missed[:10])
            if len(missed) > 10:
                missed_text += f" +{len(missed) - 10} more"
            embed.add_field(name="Missed Attacks", value=missed_text, inline=False)
        
        await self.send_message(embed=embed)
        
        # Check if we should send bonus medal recommendations
        await self.check_and_send_bonus_recommendations(season_id, cwl_day, team_size)
    
    async def check_and_send_bonus_recommendations(self, season_id: str, cwl_day: int, team_size: int):
        """Send bonus medal recommendations on day 7 (final)"""
        if cwl_day != 7:
            return
        
        if db.has_cwl_bonus_been_sent(season_id, cwl_day):
            return
        
        coleader_channel = await self.get_coleader_channel()
        if not coleader_channel:
            print("[WarMonitor] No co-leader channel configured for bonus recommendations")
            return
        
        # Get season performance
        performance = db.get_cwl_season_performance(season_id)
        if not performance:
            return
        
        # Calculate scores for ranking
        # Score = (stars * 10) + (avg_destruction * 0.5) + (three_stars * 5) - (missed_attacks * 50)
        scored = []
        for p in performance:
            days_in = p.get("days_in_roster", 0) or cwl_day
            attacks_expected = days_in
            attacks_used = p.get("attacks_used", 0)
            missed = attacks_expected - attacks_used
            
            score = (
                (p.get("total_stars", 0) * 10) +
                (p.get("avg_destruction", 0) * 0.5) +
                (p.get("three_stars", 0) * 5) -
                (missed * 50)
            )
            
            scored.append({
                **p,
                "score": score,
                "missed": missed
            })
        
        # Sort by score
        scored.sort(key=lambda x: x["score"], reverse=True)
        
        # Determine bonus slots (typically based on league, assume 7 for now)
        bonus_slots = min(7, len(scored))
        
        # Build embed
        title = "CWL Final Bonus Recommendations"
        description = "CWL is complete. Based on all 7 days, here are my recommended bonus medal recipients:"
        color = discord.Color.gold()
        
        embed = discord.Embed(title=title, description=description, color=color)
        
        # Top performers (recommended for bonus)
        recommended_lines = []
        for i, p in enumerate(scored[:bonus_slots], 1):
            stars = p.get("total_stars", 0)
            avg_dest = p.get("avg_destruction", 0)
            attacks = p.get("attacks_used", 0)
            days = p.get("days_in_roster", cwl_day)
            three_stars = p.get("three_stars", 0)
            
            line = f"**{i}. {p['player_name']}** - {stars}★ ({three_stars} perfect), {avg_dest:.0f}% avg, {attacks}/{days} attacks"
            recommended_lines.append(line)
        
        embed.add_field(
            name=f"Recommended ({bonus_slots} slots)",
            value="\n".join(recommended_lines) if recommended_lines else "No data",
            inline=False
        )
        
        # Honorable mentions (next 3)
        honorable = scored[bonus_slots:bonus_slots + 3]
        if honorable:
            honorable_lines = []
            for i, p in enumerate(honorable, bonus_slots + 1):
                stars = p.get("total_stars", 0)
                avg_dest = p.get("avg_destruction", 0)
                line = f"{i}. {p['player_name']} - {stars}★, {avg_dest:.0f}% avg"
                honorable_lines.append(line)
            embed.add_field(
                name="Honorable Mentions",
                value="\n".join(honorable_lines),
                inline=False
            )
        
        # Did not participate
        non_participants = [p for p in scored if p.get("attacks_used", 0) == 0]
        if non_participants:
            names = ", ".join(p["player_name"] for p in non_participants[:10])
            if len(non_participants) > 10:
                names += f" +{len(non_participants) - 10} more"
            embed.add_field(
                name="Did Not Participate",
                value=names,
                inline=False
            )
        
        # Players with missed attacks (but did participate)
        missed_some = [p for p in scored if 0 < p.get("attacks_used", 0) < p.get("days_in_roster", cwl_day)]
        if missed_some:
            missed_lines = [f"{p['player_name']} ({p['attacks_used']}/{p.get('days_in_roster', cwl_day)})" for p in missed_some[:5]]
            embed.add_field(
                name="Missed Some Attacks",
                value=", ".join(missed_lines),
                inline=False
            )
        
        await self.send_message(embed=embed, channel=coleader_channel)
        db.mark_cwl_bonus_sent(season_id, cwl_day)
    
    async def check_and_process_war(self):
        """Main check function - called periodically"""
        # Cleanup old data occasionally
        db.cleanup_old_war_states(days=30)
        db.cleanup_old_cwl_data(days=45)
        db.cleanup_old_chat_history(days=14, max_per_user=50)
        
        try:
            war_data, is_cwl = await self.get_current_war()
        except Exception as e:
            return
        
        # No active war
        if not war_data:
            self.last_state = "notInWar"
            self.last_war_id = None
            return
        
        state = war_data.get("state")
        war_id = self.generate_war_id(war_data)
        self.is_cwl = is_cwl
        
        # CWL-specific tracking
        cwl_day = None
        season_id = None
        league_group = None

        if is_cwl:
            league_group = await self.get_cwl_group_data()
            if league_group:
                season_id = self.generate_cwl_season_id(league_group)
                db.get_or_create_cwl_season(config.CLAN_TAG, season_id)
                
                # Determine which day this war is by matching startTime
                rounds = league_group.get("rounds", [])
                war_start_time = war_data.get("startTime", "")
                
                for round_num, round_data in enumerate(rounds, 1):
                    war_tags = round_data.get("warTags", [])
                    for war_tag in war_tags:
                        if war_tag == "#0":
                            continue
                        try:
                            round_war = await self.coc.get_cwl_war(war_tag)
                            # Match by startTime to find correct day
                            if round_war.get("startTime") == war_start_time:
                                cwl_day = round_num
                                break
                        except:
                            continue
                    if cwl_day:
                        break
                
                # Fallback if no match found
                if not cwl_day:
                    cwl_day = 1
        
        # Ensure war state exists in database
        db.upsert_war_state(war_id, state, is_cwl)
        
        # Update memory state
        if self.last_war_id != war_id:
            self.last_war_id = war_id
        self.last_state = state
        
        # Handle state transitions
        if state == "preparation":
            pass  # Nothing to do during prep

        elif state == "inWar":
            # For CWL day 2+, backfill any unrecorded previous days and grab
            # the immediate prior day's data so we can bundle its result into
            # the day-begin announcement.
            prev_day_war_data = None
            if is_cwl and cwl_day and cwl_day > 1 and season_id and league_group:
                for day in range(1, cwl_day):
                    day_war = await self.get_cwl_day_war_data(league_group, day)
                    if not day_war:
                        continue
                    if day_war.get("state") == "warEnded":
                        day_war_id = self.generate_war_id(day_war)
                        if not db.has_war_end_been_announced(day_war_id):
                            self.record_cwl_war_data(day_war, day, season_id)
                            db.mark_war_end_announced(day_war_id)
                            db.clear_reminders_for_war(day_war_id)
                    if day == cwl_day - 1:
                        prev_day_war_data = day_war

            # Announce war start (only once, persisted in DB)
            if not db.has_war_start_been_announced(war_id):
                await self.send_war_started_announcement(
                    war_data, is_cwl, cwl_day,
                    prev_day_war_data=prev_day_war_data,
                    season_id=season_id,
                )
                db.mark_war_start_announced(war_id)

            # Check reminder thresholds
            end_time = parse_coc_timestamp(war_data.get("endTime"))
            now = datetime.now(timezone.utc)
            hours_remaining = (end_time - now).total_seconds() / 3600

            for threshold in REMINDER_THRESHOLDS:
                if hours_remaining <= threshold:
                    await self.send_attack_reminder(war_data, is_cwl, threshold)

        elif state == "warEnded":
            # Announce war end (only once, persisted in DB)
            if not db.has_war_end_been_announced(war_id):
                if is_cwl and season_id and cwl_day:
                    # Always record performance so bonus recs stay correct.
                    # Only send the standalone "Day X Ended" embed for the
                    # final CWL day — earlier days get bundled into the
                    # next day's "begun" announcement.
                    self.record_cwl_war_data(war_data, cwl_day, season_id)
                    if cwl_day >= 7:
                        await self.send_cwl_war_ended_announcement(war_data, cwl_day, season_id)
                else:
                    await self.send_war_ended_announcement(war_data, is_cwl)
                db.mark_war_end_announced(war_id)
                db.clear_reminders_for_war(war_id)

# Global monitor instance
monitor: Optional[WarMonitor] = None
bot_instance: Optional["Bot"] = None

def setup_war_monitor(bot: "Bot", coc_api: CoCAPI):
    """Initialize the war monitor"""
    global monitor, bot_instance
    bot_instance = bot
    monitor = WarMonitor(bot, coc_api)
    print("[WarMonitor] Initialized")

@tasks.loop(minutes=2)
async def war_monitor_task():
    """Background task that checks war status every 2 minutes"""
    if monitor:
        await monitor.check_and_process_war()

@war_monitor_task.before_loop
async def before_war_monitor():
    """Wait for bot to be ready before starting monitor"""
    if bot_instance:
        await bot_instance.wait_until_ready()
    print("[WarMonitor] Bot is ready, starting monitor...")

def start_monitor():
    """Start the war monitor task"""
    if not war_monitor_task.is_running():
        war_monitor_task.start()
        print("[WarMonitor] Started monitoring (every 2 minutes)")

def stop_monitor():
    """Stop the war monitor task"""
    if war_monitor_task.is_running():
        war_monitor_task.cancel()
        print("[WarMonitor] Stopped monitoring")