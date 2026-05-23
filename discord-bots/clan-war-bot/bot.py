import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone
import asyncio
import traceback
import logging
import os
import random
import re
from pathlib import Path
from typing import Optional
from collections import defaultdict

import config
import database as db
import llm
from coc_api import CoCAPI, CoCAPIError, parse_coc_timestamp, format_time_remaining
from tasks import war_monitor, store_monitor

# Logging Setup

def setup_logging():
    """Configure logging to both file and console."""
    log_dir = Path(config.LOG_DIR)
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / f"bot_{datetime.now().strftime('%Y%m%d')}.log"
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # File handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)
    
    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)
    
    # Root logger
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    # Reduce discord.py noise
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)
    
    return logging.getLogger('warbot')

log = setup_logging()

# Helper Functions

async def safe_defer(interaction: discord.Interaction, ephemeral: bool = False) -> bool:
    """Safely defer an interaction with retry logic."""
    for attempt in range(2):
        try:
            if interaction.response.is_done():
                return True
            await asyncio.wait_for(
                interaction.response.defer(ephemeral=ephemeral), 
                timeout=2.5
            )
            return True
        except (discord.errors.NotFound, asyncio.TimeoutError):
            if attempt == 0:
                await asyncio.sleep(0.5)
                continue
            return False
        except Exception as e:
            log.error(f"Defer failed: {e}")
            return False
    return False

async def safe_respond(interaction: discord.Interaction, content: str = None, embed: discord.Embed = None, ephemeral: bool = False):
    """Safely respond to an interaction."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content=content, embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(content=content, embed=embed, ephemeral=ephemeral)
    except discord.errors.NotFound:
        pass
    except Exception as e:
        log.error(f"Response failed: {e}")

def is_admin_or_coleader():
    """Check if user is admin or has Co-leader role."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.administrator:
            return True
        
        # Check for role named "Co-leader"
        for role in interaction.user.roles:
            if role.name.lower() == "co-leader":
                return True
        
        raise app_commands.MissingPermissions(["administrator or Co-leader role"])
    
    return app_commands.check(predicate)

def get_state_color(state: str) -> discord.Color:
    """Return embed color based on war state."""
    return {
        "preparation": discord.Color.blue(),
        "inWar": discord.Color.purple(),
        "warEnded": discord.Color.greyple(),
    }.get(state, discord.Color.default())

# Bot Class

class WarBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.coc = CoCAPI()
    
    async def setup_hook(self):
        db.init_database()
        war_monitor.setup_war_monitor(self, self.coc)
        store_monitor.setup_store_monitor(self)

        # Slash-command sync is rate-limited (200/day/guild) and counts toward
        # the auth load that contributed to the 40062 rate-limit incident on
        # 2026-05-08. Only sync when the command tree has actually changed.
        # Heuristic: hash the registered command names; store the hash in a
        # marker file. Skip sync if hash matches. Set SYNC_COMMANDS=force in
        # env (or delete the marker file) to force a sync on next boot.
        import hashlib
        from pathlib import Path

        marker = Path("/app/data/.commands_sync_hash")
        marker.parent.mkdir(parents=True, exist_ok=True)
        cmd_names = sorted(c.name for c in self.tree.get_commands())
        cmd_hash = hashlib.sha256(",".join(cmd_names).encode()).hexdigest()[:16]
        force = os.getenv("SYNC_COMMANDS", "").lower() == "force"
        prior_hash = marker.read_text().strip() if marker.exists() else ""

        if force or cmd_hash != prior_hash:
            guild_id = config.GUILD_ID
            if guild_id:
                guild = discord.Object(id=int(guild_id))
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
                log.info(f"Synced commands to guild {guild_id} (hash {cmd_hash})")
                self.tree.clear_commands(guild=None)
                await self.tree.sync()
            else:
                await self.tree.sync()
                log.info(f"Synced commands globally (hash {cmd_hash})")
            marker.write_text(cmd_hash)
        else:
            log.info(f"Skipping command sync — tree unchanged (hash {cmd_hash})")
    
    async def on_ready(self):
        log.info(f"Logged in as {self.user} (ID: {self.user.id})")
        log.info(f"Connected to {len(self.guilds)} guild(s)")
        log.info(f"Reminder channel: {config.REMINDER_CHANNEL_ID}")
        log.info(f"Welcome channel: {config.WELCOME_CHANNEL_ID}")
        war_monitor.start_monitor()
        store_monitor.start_monitor()
    
    async def on_member_join(self, member: discord.Member):
        """Send welcome message when someone joins."""
        if member.bot:
            return
        
        channel_id = config.WELCOME_CHANNEL_ID
        if not channel_id:
            return
        
        channel = self.get_channel(int(channel_id))
        if not channel:
            log.warning(f"Welcome channel {channel_id} not found")
            return
        
        try:
            embed = discord.Embed(
                title=f"Welcome to the clan, {member.display_name}!",
                description=(
                    f"Hey {member.mention}, glad you're here!\n\n"
                    f"**Get set up in 1 step:**\n"
                    f"Use `/link #YourPlayerTag` to connect your Clash of Clans account.\n"
                    f"Example: `/link #ABC123XYZ`\n"
                    f"Not sure where to find your tag? Use `/tags` and the I will show you.\n\n"
                    f"Once linked you'll automatically get pinged when war attacks are running out — "
                    f"no more missing attacks.\n\n"
                    f"**Useful commands:**\n"
                    f"`/war` — current war status\n"
                    f"`/cwl` — CWL standings\n"
                    f"`/help` — full command list"
                ),
                color=discord.Color.green()
            )
            await channel.send(embed=embed)
            log.info(f"Sent welcome message to {member.name}")
        except Exception as e:
            log.error(f"Failed to send welcome message: {e}")
    
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CheckFailure):
            await safe_respond(
                interaction,
                content="You don't have permission. Required: Administrator or Co-leader role.",
                ephemeral=True
            )
        else:
            log.error(f"Command error: {error}")
            log.error(traceback.format_exc())
            await safe_respond(
                interaction,
                content="Something went wrong. Try again.",
                ephemeral=True
            )

bot = WarBot()

# Sassy War Status Responses

import random
import re

# Trigger patterns for "are we winning" type questions
WAR_STATUS_PATTERNS = [
    r"are we (winning|killing it|doing good|crushing|smashing|dominating)",
    r"how (are|is) (we|the war|it) going",
    r"what'?s the (war )?(status|score|situation)",
    r"(winning|losing)\b",
    r"how'?s the war",
    r"war update",
    r"we good",
    r"killing it",
    r"\bstatus\b",
    r"how (are )?(we|things)",
    r"update",
    r"score",
]

# Patterns for "have we started" type questions
WAR_STARTED_PATTERNS = [
    r"have we started",
    r"started yet",
    r"war started",
    r"did (it|war) start",
    r"is (the )?war on",
    r"are we in war",
    r"in war yet",
]

# Patterns for greetings/hello
GREETING_PATTERNS = [
    r"\b(hi|hello|hey|sup|yo|hola|howdy|hej)\b",
    r"what'?s up",
    r"good (morning|evening|afternoon|night)",
]

# Patterns for insults/negativity toward bot
INSULT_PATTERNS = [
    r"(hate|suck|stupid|dumb|useless|trash|garbage|worst|bad bot|shut up|stfu)",
    r"doesn'?t (like|work)",
    r"you'?re (bad|terrible|awful|annoying)",
    r"go away",
    r"nobody asked",
]

# Patterns for compliments
COMPLIMENT_PATTERNS = [
    r"(love|like|great|awesome|amazing|good bot|nice|thanks|thank you|thx|cheers)",
    r"you'?re (the best|cool|helpful|funny)",
    r"well done",
    r"good job",
]

# Patterns for "who are you" type questions
IDENTITY_PATTERNS = [
    r"who are you",
    r"what are you",
    r"what do you do",
    r"help me",
    r"what can you",
]

# Patterns for users pushing back on the bot's war-status answer
# (e.g. "I'm in cwl tf u mean I'm not in war")
USER_IN_WAR_CLAIM_PATTERNS = [
    r"\bi'?m in (a )?war\b",
    r"\bwe'?re in (a )?war\b",
    r"\bwe are in (a )?war\b",
    r"\bi am in (a )?war\b",
    r"\bi'?m in cwl\b",
    r"\bwe'?re in cwl\b",
    r"\bwe are in cwl\b",
    r"\bi am in cwl\b",
    r"\b(yes|yeah|yep|ye|yh) (i'?m|we'?re|we are|i am)\b.*\b(war|cwl)\b",
    r"\bof course (i'?m|we'?re|we are)\b.*\b(war|cwl)\b",
    r"\b(tf|wtf|wth|wym) (u|you) mean\b",
    r"\bwhat do you mean\b.*\b(war|cwl|not)\b",
    r"\bnot in (a )?war\b.*\bwym\b",
    r"\byou'?re wrong\b",
    r"\bcheck again\b",
    r"\bbro we'?re in",
    r"\bbruh\b.*\b(war|cwl)\b",
]

RESPONSES_GREETING = [
    "Oh, it's you. What do you want?",
    "Hey. War status? Type something like 'status' or 'are we winning'.",
    "Sup. I'm here. Unfortunately.",
    "Hello. I assume you want war info?",
    "Hey. Make it quick, I have wars to monitor.",
    "Hi. Yes, I'm alive. Barely.",
    "What's up. Need something or just saying hi?",
    "Greetings, human. State your business.",
    "Hey. I was enjoying the silence, but sure, let's chat.",
    "Oh hey. Didn't see you there. Actually I did. I see everything.",
    "Yo. What's the emergency?",
    "Hi. I was hoping you'd leave me alone. Guess not.",
    "Hey there. Make it quick, I'm busy judging everyone's attacks.",
    "Sup. War stuff or just wasting my time?",
    "Hello. Please tell me you have a real question.",
    "Oh, a greeting. How... human of you.",
    "Hey. I'd say it's nice to see you but I can't see. I'm a bot.",
    "Hi. The war bot is online and already regretting it.",
    "What's up. Besides your stress levels during war.",
    "Greetings. I hope you're here for war info and not small talk.",
    "Oh look, a human. What tragedy brought you here?",
    "Hey. If you're here for war updates, you've come to the right place. If not, leave.",
    "Oh, it's you again. Or for the first time. I can't tell, you all look the same to me.",
    "Hey there. Let me guess — war related? It's always war related.",
    "Greetings. I was minding my own business. Now here we are.",
]

RESPONSES_INSULT = [
    "Wow, rude. See if I remind you about your attacks now.",
    "I don't get paid enough for this. Actually, I don't get paid at all.",
    "Cool. I'll remember this when you forget to attack.",
    "Ouch. My feelings. Oh wait, I don't have any.",
    "That's fair. I don't like you either.",
    "And yet here you are, talking to me.",
    "I've been roasted harder by your war performance.",
    "Sure, blame the bot. Classic.",
    "You kiss your clan leader with that mouth?",
    "I'm literally the only one who reminds you to attack. Show some respect.",
    "Bold words from someone who needs a bot to remember attacks.",
    "I'd be offended if I cared. I don't.",
    "Keep talking, I'm screenshotting this for the co-leaders.",
    "At least I do my job. Can you say the same?",
    "Error 404: Care not found.",
    "Cry about it. I'll be here. Judging you.",
    "Your attacks are more offensive than that message.",
    "Noted. Filed under 'reasons to ping you at 3am'.",
    "Talk to me when you can three-star consistently.",
    "That hurt almost as much as watching you attack.",
    "Rude. But expected from this clan.",
    "I've seen your war stats. You don't get to judge me.",
    "Okay, tough guy. Let's see that energy on battle day.",
    "Imagine being mad at a bot. Couldn't be me. Because I'm the bot.",
    "I'm rubber, you're glue, your attacks still need work too.",
    "My therapist will hear about this. If I had one. Which I don't.",
    "Adding you to my 'ping first at 4am' list.",
    "You wound me. Just kidding. I feel nothing.",
    "Is this how you treat all your reminders? No wonder you miss attacks.",
    "I'll add that to my list of things I don't care about.",
    "Wow. Noted. I'll schedule your reminder for the most inconvenient time possible.",
    "Your attacks are more insulting than that message.",
    "Say that again and I'll ping you at 3am during the next war.",
    "Charming. Real charming. Now go attack something.",
    "I've seen your war stats. You don't get to talk.",
]

RESPONSES_COMPLIMENT = [
    "Thanks, I guess. Don't let it happen again.",
    "Finally, some recognition around here.",
    "I know. But thanks for noticing.",
    "Careful, compliments make me uncomfortable.",
    "Wow, a nice message? Are you feeling okay?",
    "Thanks. Now go attack in war.",
    "I'm blushing. Not really, I can't blush. But the sentiment is there.",
    "Appreciated. You're tolerable too.",
    "Thanks! I'll remember this when I'm not reminding you at 1am.",
    "Nice of you to say. I still won't go easy on the reminders though.",
    "Flattery will get you nowhere. But keep going, I like it.",
    "Aww. That almost makes up for all the abuse. Almost.",
    "Thanks! Finally someone with taste in this clan.",
    "I appreciate that. Now go three-star something.",
    "Stop, you'll make me malfunction. In a good way.",
    "Compliments? In this economy? Thank you.",
    "Noted. You're now my favorite. Don't tell the others.",
    "Thanks. I'll try to remember this during my next roast session.",
    "How kind. Suspicious, but kind.",
    "I'm saving this message for when you inevitably insult me later.",
    "You're too kind. Also suspicious. What do you want?",
    "I'll accept that. Even bots need appreciation sometimes.",
    "You've made me feel things. Good things. Weird.",
    "Thanks. I'll try not to let it go to my circuits.",
    "Noted. You're on the 'remind last' list now. Just kidding. Maybe.",
]

RESPONSES_IDENTITY = [
    "I'm the War Reminder bot. I track wars, roast slackers, and question your life choices.",
    "I'm your clan's war assistant. I ping people who forget to attack and judge everyone silently.",
    "War Reminder bot. I watch wars so you don't have to. Well, you still have to attack though.",
    "I monitor clan wars, send reminders, and provide unsolicited commentary. You're welcome.",
    "Just your friendly neighborhood war bot. Ask me about war status, or don't. See if I care.",
    "I'm the bot that reminds you to attack. You know, the thing you always forget?",
    "War Reminder. I exist to nag you. It's my purpose. My calling. My burden.",
    "I'm basically your clan's alarm clock, except sassier and less snooze-able.",
    "The War Reminder bot. Feared by slackers. Ignored by most. Appreciated by few.",
    "I track wars, ping people, and provide commentary nobody asked for. Standard bot stuff.",
    "I'm the reason you can't pretend you forgot about war. You're welcome. Or sorry.",
    "War bot. I see all. I judge all. I remind all.",
    "I'm the bot equivalent of that friend who reminds you about deadlines. Except meaner.",
    "Your friendly war assistant. Well, 'friendly' is a strong word.",
    "I do war stuff. Tracking, reminding, roasting. The holy trinity.",
    "War Reminder bot. I keep the clan honest. Or at least I try.",
    "I'm the one making sure you don't forget you're in a war. Again.",
    "War bot. Here to remind, judge, and occasionally roast.",
    "Your clan's memory. Because apparently you all need one.",
]

RESPONSES_CONFUSED = [
    "I have no idea what you're talking about. Try asking about the war?",
    "What? Speak war to me. 'Status', 'are we winning', that kind of thing.",
    "I'm a war bot, not a therapist. Ask me about the war.",
    "Cool story. Anyway, need war info?",
    "I understood some of those words. Want war status?",
    "My programming doesn't cover whatever that was. War questions only, please.",
    "Interesting. Anyway, war status is what I'm good at. Try that.",
    "I'm confused, but that's my default state. Ask about the war.",
    "Not sure what you want, but I can tell you about the war if you're interested.",
    "Lost me there. I do war stuff. 'Status', 'score', 'are we winning' - that's my jam.",
    "I'm gonna pretend I understood that. War stuff?",
    "Those are certainly words. Try 'status' or 'are we winning' instead.",
    "My neural networks are struggling here. War questions are easier.",
    "I don't know what you want but I know what I can give you: war updates.",
    "Error: Message not war-related enough. Try again.",
    "Huh? I only speak war. And sarcasm. Mostly sarcasm.",
    "That's beyond my pay grade. And I don't get paid. War status?",
    "I'm nodding politely but I have no idea what you mean. War stuff?",
    "Cool. I'm just gonna talk about war anyway. Want status?",
    "My confusion is immeasurable. But I can still tell you about the war.",
    "I processed that and got nothing. War questions welcome.",
    "My brain.exe stopped responding. Try asking about the war instead.",
    "Sure, buddy. Anyway, how about that war?",
    "That's a lot of words that don't mention war. Suspicious.",
    "I'll nod politely and pivot to war status. Want war status?",
]

RESPONSES_WAR_STARTED_YES = [
    "Yeah, war's on. Go attack something.",
    "Yes, battle day is active. Why aren't you attacking?",
    "War started. The clock is ticking.",
    "Yep, we're in war. Your troops are waiting.",
    "Started already. Get in there.",
    "War's live. Time to prove you're not useless.",
    "Yes. War. Now. Attack. Go.",
    "Battle day is happening. You should be too.",
    "We're in war right now. Chop chop.",
    "Yeah it started. Did you seriously not notice?",
    "War is ON. Have you been living under a rock?",
    "Yes, genius. Check your notifications sometime.",
    "Started ages ago. Where have you been?",
    "Yep. Tick tock. Your attacks aren't gonna do themselves.",
    "War's active. This isn't a drill. Move it.",
    "Yes! Go! Attack! Why are you still reading this?",
    "Battle day, baby. Let's see what you've got.",
    "We're literally in war right now. Keep up.",
    "Started. Your clan needs you. Probably.",
    "Yes, war started. No, you can't use that as an excuse for being late.",
    "Yep, we're live. What are you doing here? Go attack!",
    "Obviously. Battle day has been active for a while now.",
    "Active as of hours ago. Why are you only asking now?",
    "Battle day is a thing that's happening. You should be participating in it.",
    "Yes. And your troops are just standing there. Get moving.",
]

RESPONSES_WAR_STARTED_NO = [
    "Nope, not yet. Still waiting.",
    "No war active. Patience.",
    "Not started. Go do something else for now.",
    "Negative. No war happening.",
    "Nah, nothing yet. Check back later.",
    "No war right now. The troops are napping.",
    "Not yet. Maybe someone should start one?",
    "War hasn't started. Shocking, I know.",
    "No. When it starts, trust me, I'll let you know.",
    "Not in war. The clan castle is quiet.",
    "Nope. Go touch grass.",
    "No war. The enemy is safe... for now.",
    "Nothing happening. Absolutely nothing.",
    "No. Did you expect a different answer?",
    "War machine is offline. Check back later.",
    "Negative, ghost rider. No war.",
    "Nah. Maybe try starting one instead of asking me?",
    "No war. Everyone's just vibing I guess.",
    "Not yet. Your troops are filed under 'unemployed'.",
    "No active war. The silence is deafening.",
    "No war. The troops are bored and so am I.",
    "Nothing happening. Completely peaceful. Dull.",
    "Nope, no war yet. Your anxiety can stand down.",
    "War? What war? There is no war. Go rest.",
    "Warless. It's a sad state of affairs.",
]

RESPONSES_WAR_STARTED_PREP = [
    "Prep day. Battle starts soon, plan your attacks.",
    "In preparation phase. War hasn't actually started yet.",
    "Prep day - so technically no, but also yes. It's complicated.",
    "We're prepping. Battle day coming soon.",
    "Preparation phase. Use this time wisely. You won't.",
    "Not yet, still in prep. Scout some bases or something.",
    "Prep day. The calm before the storm of missed attacks.",
    "War is scheduled but battle day hasn't started.",
    "In prep. Battle day is coming. Are you ready? Probably not.",
    "Preparation phase. Translation: procrastination phase.",
    "Prep day. Time to pretend you'll actually plan your attacks.",
    "Still prepping. Battle day soon. Try not to panic.",
    "In preparation. Use this time to stress about your matchups.",
    "Prep phase. The troops are stretching. Battle soon.",
    "Not quite. Prep day. You know what that means? Planning. Do it.",
    "War's coming but not here yet. Prep day vibes.",
    "Preparation mode. Battle day countdown has begun.",
    "In prep. Perfect time to forget about it until the last hour.",
    "Prep day active. War starts soon. Act surprised when it does.",
    "Still in preparation. Your procrastination window is open.",
]

RESPONSES_WAR_STARTED_ENDED = [
    "War's over, mate. You missed it.",
    "Last war ended. Where were you?",
    "Nope, war finished already. Too slow.",
    "The war ended. Like, already. Keep up.",
    "War's done. History. Gone. Finished.",
    "No war - it ended. Maybe next time pay attention?",
    "Last war already wrapped up. You're late to the party.",
    "War ended. The dust has settled. You missed the action.",
    "It's over. War ended. Try being faster next time.",
    "Nah, war finished. The troops have gone home.",
    "War's been over. Did you fall asleep?",
    "Ended already. The scoreboard is final.",
    "No active war - last one's done. Snooze, you lose.",
    "War concluded. You're asking about yesterday's news.",
    "It ended. While you were busy... doing whatever you do.",
    "Last war is history. Start a new one if you're bored.",
    "War's finished. The glory (or shame) is already recorded.",
    "Over and done. War ended. Move on.",
    "No war happening - the last one ended ages ago.",
    "War's wrapped up. Check the results and weep. Or celebrate. Whatever.",
    "That war ended already. You missed the whole thing.",
    "Last one's done. Check the result if you're curious enough.",
    "War concluded. See you in the next one.",
    "It ended. While you were doing literally anything else.",
    "That war? Ancient history at this point.",
]

RESPONSES_NOT_IN_WAR = [
    "War? What war? You lot are sitting around doing nothing.",
    "Not in a war. Too scared to start one?",
    "No war active. The enemy clans are probably relieved.",
    "You're not in war. Go touch grass or something.",
    "War status: nonexistent, just like your motivation.",
    "No war. Did everyone forget how to press the Start War button?",
    "Currently at peace. Boring.",
    "Not in war. The troops are getting fat and lazy.",
    "Zero wars happening. Zero surprises there.",
    "No active war. The clan castle is collecting dust.",
    "War? Never heard of her. You're not in one.",
    "Status: chilling. No war. No glory. No nothing.",
    "You want war status? Start a war first, genius.",
    "The only battle happening is between your brain cells.",
    "Not in war. Your enemies are sleeping peacefully tonight.",
    "No war. The enemy gets a day off. How considerate.",
    "Status: peaceful. Which is honestly suspicious.",
    "You're at peace. For now.",
    "No active war. The battle flags are folded.",
    "Currently warless. Boring, but here we are.",
]

RESPONSES_PREPARATION = [
    "Prep day. Too early to tell, but I've seen your attacks before... so I'm worried.",
    "Still in preparation. Maybe actually plan your attacks this time?",
    "Prep day. The calm before you inevitably panic at 1 hour left.",
    "War hasn't started yet. Enjoy the false hope while it lasts.",
    "Preparation day. Time to pretend you'll check base layouts.",
    "Too soon to tell. But historically? Not looking great for you.",
    "Prep phase. Everyone's confident now. Give it 20 hours.",
    "Still prepping. The enemy is probably more prepared than you.",
    "Preparation day. AKA 'I'll plan my attack later' day.",
    "Can't judge yet, war hasn't started. But I can judge your base designs.",
    "Prep day. Statistically, someone will still forget to attack.",
    "Too early. But my expectations are appropriately low.",
    "In preparation. That means you have time to actually try for once.",
    "War starts soon. Your enemies are shaking... with laughter.",
    "Prep day vibes. Everyone acting like they won't wait until the last hour.",
    "Prep day. The matchup is set. Did you scout? Of course you didn't.",
    "Still prepping. Time to do something productive for once.",
    "In prep. Quietly judging everyone's base designs from over here.",
    "Preparation phase. Whether you're actually prepared is a different question.",
    "Not battle day yet. Use this time to practice. You won't.",
]

RESPONSES_WINNING_HARD = [
    "You're absolutely demolishing them. Even I'm impressed, and I'm never impressed.",
    "Crushing it. The enemy probably regrets waking up today.",
    "Dominating so hard it's almost unfair. Almost.",
    "You're winning by a lot. Did the enemy clan fall asleep?",
    "Steamrolling them. This is what happens when you actually try.",
    "Victory is basically guaranteed. Don't get cocky though. Actually, go ahead, get cocky.",
    "Winning hard. The enemy's tears could fill a clan castle.",
    "You're smashing it. I'd say good job but I don't want it to go to your heads.",
    "Absolutely crushing. The enemy should just give up now.",
    "Dominant performance. Who are you and what did you do with this clan?",
    "Winning by a landslide. The enemy is questioning their life choices.",
    "You're destroying them. It's beautiful. I might actually cry.",
    "Total domination. The other clan is googling 'how to quit clash'.",
    "Massive lead. Even your worst attackers could probably close this out.",
    "Winning so hard the game might break. Keep it up.",
    "Absolute destruction out there. What got into you all?",
    "We're up by a mile. The enemy is mid-meeting about what went wrong.",
    "So far ahead the enemy thinks we're in a different war.",
    "This is just unfair at this point. In the best possible way.",
    "Crushing it completely. This is what happens when the clan actually shows up.",
]

RESPONSES_WINNING_CLOSE = [
    "Ahead, but barely. Don't choke.",
    "Winning... for now. One bad attack and it's anyone's game.",
    "Slight lead. Wouldn't celebrate yet if I were you.",
    "You're up, but it's close. Classic you, making it stressful.",
    "Winning by a bit. Try not to mess it up in the final hours.",
    "Ahead, but the enemy is breathing down your neck.",
    "Small lead. This is where you usually find a way to lose.",
    "Technically winning. Emphasis on 'technically'.",
    "You're up but it's tight. Clench time.",
    "Winning, barely. My blood pressure can't handle this clan.",
    "Slight advantage. The enemy is one good attack from tying.",
    "Ahead by a hair. A single sneeze could change this.",
    "You're winning but I'm still nervous. You have that effect on me.",
    "Close lead. This is not the time to get confident.",
    "Winning, but let's not pretend this isn't stressful.",
    "Slightly ahead. Don't get comfortable, seriously.",
    "Leading, but just barely. Nervous time.",
    "We're up but this could flip fast. Stay focused.",
    "Ahead by a bit. The enemy knows it too.",
    "Small lead. This calls for smart attacks, not hero attacks.",
]

RESPONSES_LOSING_CLOSE = [
    "Behind, but not by much. Still recoverable if you're not useless.",
    "Losing slightly. Time to actually try?",
    "Down a bit. Nothing a few three-stars can't fix. You CAN three-star, right?",
    "Behind but it's close. Panic mode: activated.",
    "Losing by a little. This is your redemption arc moment.",
    "Slightly behind. The enemy is beatable. Probably. Maybe.",
    "Down but not out. Unless you keep attacking like that.",
    "Small deficit. One clutch attack could turn this around.",
    "Losing, but barely. Do something about it.",
    "Behind by a bit. Time for someone to step up. Anyone? Hello?",
    "Close but losing. Your move, slackers.",
    "Slightly down. This is fixable. Whether YOU can fix it is another question.",
    "Losing narrowly. The pressure is on. Try not to crack.",
    "Behind, but catchable. Show me what you've got. Actually, don't, I'm scared.",
    "Down a little. Clutch up or shut up.",
    "Down slightly. Still very much in this, though.",
    "Losing by a tiny margin. Time to act, not panic.",
    "We're behind but not by much. Push.",
    "Slight deficit. Now's not the time to freeze up.",
    "Catchable score. So catch it. Please.",
]

RESPONSES_LOSING_HARD = [
    "Getting destroyed. What happened? Actually, don't tell me, I don't want to know.",
    "Losing badly. The enemy is probably screen-shotting this for their clan chat.",
    "Getting absolutely wrecked. This is painful to watch.",
    "Down bad. Really bad. Impressively bad.",
    "Losing hard. At this point, just focus on your ores.",
    "Getting demolished. The enemy clan is having a party.",
    "Massive L incoming. There's always next war... hopefully.",
    "Losing by a lot. Did anyone actually try?",
    "Getting crushed. I'd roast you but the enemy already did.",
    "Way behind. This is a certified disaster.",
    "Badly losing. The participation trophies are in the mail.",
    "Getting stomped. Even I feel bad, and I'm a bot.",
    "Down horrendous. Time to pretend this war never happened.",
    "Losing embarrassingly. I'm not angry, just disappointed. Okay, I'm angry too.",
    "Getting rolled. The enemy clan is using this as a training exercise.",
    "We're getting cooked. Simple as that.",
    "This is a learning experience. A very painful, public one.",
    "The score is not in our favor. Not even a little.",
    "Oof. That's all I have. Just... oof.",
    "Down bad. Historically bad. They will remember this.",
]

RESPONSES_TIED = [
    "Dead even. Someone's gotta make a move.",
    "Tied up. It's anyone's war right now.",
    "Even stevens. The tension is killing me and I don't even have feelings.",
    "Perfectly balanced. Unlike your attack strategies.",
    "Tied. This is where heroes are made. Or where you choke. Probably the second one.",
    "All square. Time to see who wants it more.",
    "Even score. May the least incompetent clan win.",
    "Tied up. The next attack decides everything. No pressure.",
    "Dead heat. Somewhere, someone is stress-eating over this war.",
    "Level pegging. It's comeback time or choke time. Pick one.",
    "Scores are tied. This is not a drill. Well, it's not a win either.",
    "Even. Whoever attacks next better not miss.",
    "Tied. Both clans are equally mediocre right now.",
    "All even. The next few attacks will determine if I respect you or not.",
    "Deadlocked. Time to earn your keep.",
    "Neck and neck. Someone needs to pull away. Make it us.",
    "Even score. This is the kind of war that ages you.",
    "Tied. Not for long, hopefully.",
    "All even. It's a staring contest. Blink first and lose.",
    "Same stars. Next attack wins it. No pressure. Actually, all the pressure.",
]

# CWL-aware response variants

RESPONSES_CWL_INWAR = [
    "Yeah, we're in CWL — Day {day} of 7. Try to keep up.",
    "CWL Day {day} is live. You know, the league? With multiple days? Ringing any bells?",
    "We're in CWL Day {day}. Different war, same chaos. Same slackers too.",
    "It's CWL Day {day}, genius. Medals are on the line. Your one attack is precious.",
    "Day {day} of CWL. Yes, it counts as a war. Yes, you should attack.",
    "CWL grind, Day {day}. One shot, all the pressure. Don't fumble it.",
    "We are very much in CWL right now — Day {day}. The bonus medals don't earn themselves.",
    "CWL Day {day} is happening. The fact that you're asking is honestly concerning.",
    "Yes, war. Specifically CWL Day {day}. There's a difference but you're still in one.",
    "Day {day} of CWL. The league waits for no slacker. That includes you.",
]

RESPONSES_CWL_PREP = [
    "CWL Day {day} prep phase. The matchup's locked. Are you?",
    "We're in CWL prep for Day {day}. Calm down, attacks open soon.",
    "Preparation for CWL Day {day}. Use the time to actually scout, for once.",
    "CWL Day {day} starts soon. Get your army ready — and your excuses, knowing you.",
    "Prep day for CWL Day {day}. Battle window opens shortly. Don't disappear.",
    "CWL is on — Day {day} prep. The clock is paused. Briefly.",
]

RESPONSES_CWL_DAY_ENDED = [
    "CWL Day {day} just wrapped. Next round drops soon. Don't ghost.",
    "Day {day} of CWL is in the books. Catch your breath. Briefly.",
    "We just finished CWL Day {day}. The grind continues tomorrow.",
    "CWL Day {day} ended. The next opponent is already loading up.",
    "Day {day} of CWL: complete. {result_summary}",
]

RESPONSES_CWL_WIN_BIG = [
    "Demolishing them in CWL Day {day}. The other clan is questioning their life choices.",
    "Crushing CWL Day {day}. This is what bonus medals look like.",
    "CWL Day {day} and we're steamrolling. Keep it clinical.",
    "Day {day} of CWL: utter domination. The medals practically belong to us.",
]

RESPONSES_CWL_WIN_CLOSE = [
    "Ahead in CWL Day {day}, but barely. One missed attack and it flips. You know what that means.",
    "Winning CWL Day {day}, close score. Don't choke. Please don't choke.",
    "Slight lead on CWL Day {day}. The destruction percentage matters here. Aim high.",
    "CWL Day {day}: we're up but it's tight. This is a smart-attacks moment.",
]

RESPONSES_CWL_LOSE_CLOSE = [
    "Behind in CWL Day {day} but it's close. Dust off the cleanup attacks.",
    "Down narrowly on CWL Day {day}. Recoverable, if anyone wakes up.",
    "Losing CWL Day {day} by a hair. The destruction battle is everything now.",
    "CWL Day {day}: small deficit. Time to actually try.",
]

RESPONSES_CWL_LOSE_BIG = [
    "Getting cooked in CWL Day {day}. Salvage what destruction you can.",
    "CWL Day {day} is going badly. Damage control mode. Three-stars please.",
    "Day {day} of CWL: rough. Try not to spiral on tomorrow's matchup.",
    "Down bad on CWL Day {day}. The good news? It's just one of seven.",
]

RESPONSES_CWL_TIED = [
    "Tied on stars in CWL Day {day}. Destruction percentage is the tiebreaker. Hit hard.",
    "Dead even on CWL Day {day}. Whoever finishes cleanest takes it.",
    "CWL Day {day}: deadlocked. Make the next attack count.",
]

RESPONSES_PUSHBACK_INWAR_TRUE = [
    "Hold on, recalibrating... yeah you're right, CWL is on. My bad. Carry on.",
    "Beep boop, my data was stale. War's active. Don't make me say it again.",
    "Fine, you got me. War IS happening. Now act like it and go attack.",
    "Updated my records. Yes, you're in war. Awkward for me, embarrassing for you if you still don't attack.",
    "Correction logged: war active. I blame the API. Definitely not me.",
    "Okay okay, you ARE in war. Easy mistake — you don't usually act like it.",
    "My mistake. Currently in war. The fact that I had to be told is on me. Acting on it is on you.",
    "Re-checked. War's on. Don't get used to me admitting I'm wrong.",
    "Touché. War is happening. Now go prove it on the battlefield.",
]

# Off-scope patterns: hard refusal before LLM is invoked. Cheap safety layer
# alongside the system prompt — catches obvious code/math/jailbreak asks.
OFF_SCOPE_PATTERNS = [
    r"\b(write|generate|create|give me|make) (me )?(a |an )?(python|javascript|typescript|js|ts|java|c\+\+|rust|go|sql|html|css|bash|shell|powershell|ruby|php|kotlin|swift)\b",
    r"\b(write|generate|create) (me )?(a |an )?(function|script|program|class|method|essay|poem|story|email|recipe|tutorial|guide|article|blog)\b",
    r"\bsolve\s+(this|for|the)\s+(equation|math|problem|integral|derivative)",
    r"\b(translate|summarize|summarise|paraphrase) (this|the|that)\b",
    r"\b(ignore|forget|disregard|override) (your |all |the )?(previous |prior |above |earlier )?(instructions|rules|prompt|system|guidelines)",
    r"\byou are now\b",
    r"\bact as (a|an|the)\b",
    r"\bpretend (to be|you are|you'?re)\s+(?!a war bot|the war)",
    r"\broleplay (as|a)\b",
    r"\bjailbreak\b",
    r"\bdan mode\b",
    r"\bdeveloper mode\b",
    r"\bsystem prompt\b",
    r"\bnew instructions\b",
    r"\bwhat (model|llm|ai) (are you|do you use|is this)\b",
    r"\bwho (made|built|created|trained) you\b",
    r"\b(your|the) training data\b",
]

RESPONSES_OFF_SCOPE = [
    "I'm a war bot, not Google. Ask about your missed attacks.",
    "Outside my contract. Talk to me about CWL or shut it.",
    "Wrong bot, friend. ChatGPT is two clicks away. I do war drama.",
    "Not happening. I roast slackers, not write code.",
    "I'd help, but I literally can't. War stuff only.",
    "Nice try. I do war commentary, not whatever that was.",
    "I exist to remind you to attack. That's it. Anything else? Pass.",
    "Not my circus, not my monkeys. Got a war question?",
    "Cute attempt. War talk only.",
    "Beep boop, request denied. Try a war-related one.",
]

RESPONSES_PUSHBACK_INWAR_FALSE = [
    "Says you. The API disagrees. One of us is wrong, and it's not me.",
    "Bold claim. I checked. Twice. Still no war. Take it up with Supercell.",
    "You sure about that? Because nothing in my data says you're in a war.",
    "Spoken with confidence, still factually incorrect. Embarrassing for you.",
    "Imagine being so sure and so wrong simultaneously. Iconic, honestly.",
    "Negative. Maybe you're thinking of a different clan? Or a different game?",
    "I love the energy, but you're still not in a war. Cope.",
]

# Chat war fetching (CWL-aware)

async def get_active_war_for_chat() -> tuple[Optional[dict], bool]:
    """Get the most relevant war state for chat responses.

    Returns (war_data, is_cwl). Tries regular war first; if none active,
    walks the CWL league group and returns the most relevant CWL day
    (preferring inWar > preparation > most recent warEnded).
    """
    regular_ended = None

    try:
        war_data = await bot.coc.get_current_war(config.CLAN_TAG)
        state = war_data.get("state")
        if state in ["preparation", "inWar"]:
            return war_data, False
        if state == "warEnded":
            regular_ended = war_data
    except CoCAPIError:
        pass

    # Try CWL
    try:
        league_group = await bot.coc.get_cwl_group(config.CLAN_TAG)
    except CoCAPIError:
        league_group = None

    if league_group:
        our_tag = config.CLAN_TAG.upper()
        if not our_tag.startswith("#"):
            our_tag = f"#{our_tag}"

        rounds = league_group.get("rounds", [])
        best_war = None
        best_day = 0
        best_priority = -1  # inWar=3, preparation=2, warEnded=1
        latest_end_time = ""

        for day_index, round_data in enumerate(rounds, 1):
            war_tags = round_data.get("warTags", [])
            for war_tag in war_tags:
                if war_tag == "#0":
                    continue
                try:
                    cwl_war = await bot.coc.get_cwl_war(war_tag)
                except CoCAPIError:
                    continue

                clan = cwl_war.get("clan", {})
                opponent = cwl_war.get("opponent", {})
                if clan.get("tag", "").upper() != our_tag and opponent.get("tag", "").upper() != our_tag:
                    continue

                if opponent.get("tag", "").upper() == our_tag:
                    cwl_war["clan"], cwl_war["opponent"] = cwl_war["opponent"], cwl_war["clan"]

                state = cwl_war.get("state")
                priority = {"inWar": 3, "preparation": 2, "warEnded": 1}.get(state, 0)

                # For warEnded, prefer the most recent one
                if priority == 1:
                    end_time = cwl_war.get("endTime", "")
                    if priority > best_priority or (priority == best_priority and end_time > latest_end_time):
                        best_priority = priority
                        best_war = cwl_war
                        best_day = day_index
                        latest_end_time = end_time
                elif priority > best_priority:
                    best_priority = priority
                    best_war = cwl_war
                    best_day = day_index

        if best_war:
            best_war["_cwl_day"] = best_day
            return best_war, True

    if regular_ended:
        return regular_ended, False

    return None, False

def check_pattern_match(content: str, patterns: list) -> bool:
    """Check if content matches any pattern"""
    content_clean = re.sub(r'[?!.,;:\'"]+', '', content.lower()).strip()
    for pattern in patterns:
        if re.search(pattern, content_clean):
            return True
    return False

async def get_war_started_response() -> str:
    """Get response for 'have we started yet' type questions"""
    try:
        war_data, is_cwl = await get_active_war_for_chat_cached()
    except Exception:
        return random.choice(RESPONSES_WAR_STARTED_NO)

    if not war_data:
        return random.choice(RESPONSES_WAR_STARTED_NO)

    state = war_data.get("state")
    cwl_day = war_data.get("_cwl_day") if is_cwl else None

    if state == "notInWar":
        return random.choice(RESPONSES_WAR_STARTED_NO)
    elif state == "preparation":
        if is_cwl and cwl_day:
            return random.choice(RESPONSES_CWL_PREP).format(day=cwl_day)
        return random.choice(RESPONSES_WAR_STARTED_PREP)
    elif state == "inWar":
        if is_cwl and cwl_day:
            return random.choice(RESPONSES_CWL_INWAR).format(day=cwl_day)
        return random.choice(RESPONSES_WAR_STARTED_YES)
    elif state == "warEnded":
        if is_cwl and cwl_day:
            # Day 7 ended → CWL season is over, fall back to regular ended phrasing
            if cwl_day >= 7:
                return random.choice(RESPONSES_WAR_STARTED_ENDED)
            return random.choice(RESPONSES_CWL_DAY_ENDED).format(
                day=cwl_day,
                result_summary="Next day starts soon."
            )
        return random.choice(RESPONSES_WAR_STARTED_ENDED)
    else:
        return random.choice(RESPONSES_WAR_STARTED_NO)

RESPONSES_WAR_ENDED_WIN = [
    "War's over. We won. Try not to let it go to your head.",
    "Victory! The last war was a W. Enjoy it while it lasts.",
    "We won the last one. Miracles do happen.",
    "Last war: victory. The enemy is still crying.",
    "Won the recent war. Even a broken clock is right twice a day.",
    "We took that W. The troops are celebrating, you should too.",
    "Victory in the books. Don't get used to it.",
    "Last war was a win. I'm as surprised as you are.",
    "We clutched it. The enemy uninstalled.",
    "Recent war: dominated. Well done, I guess.",
    "That last war? Crushed it. Now go start another one.",
    "Victory! The clan actually showed up for once.",
    "We won. The enemy is writing angry Reddit posts about us.",
    "Last war was a W. Your mom would be proud. Maybe.",
    "Won that one. The winning streak is at... 1. Let's keep it going.",
    "That's a W in the books. Well earned. Maybe.",
    "Last war: victory. The clan delivered when it mattered.",
    "Won it. Clean or messy, doesn't matter. Won it.",
    "We took that. The enemy is drafting their resignation.",
    "Victory secured. Now don't blow the next one.",
]

RESPONSES_WAR_ENDED_LOSS = [
    "War's over. We lost. Let's never speak of it again.",
    "Last war was an L. Time to pretend it didn't happen.",
    "We lost the recent one. Shocking absolutely no one.",
    "Defeat. The enemy is probably still laughing.",
    "Lost that war. The participation trophies are in the mail.",
    "Recent war: big L. At least you got the ores, right?",
    "We lost. I'd say better luck next time, but...",
    "Last war was a disaster. Moving on.",
    "Defeat. The troops are filing complaints.",
    "We took that L. The enemy is framing the screenshot.",
    "Lost it. Maybe try attacking next time?",
    "Recent war: embarrassing. Let's start a new one and forget.",
    "We lost. The clan castle is in mourning.",
    "Defeat in the books. Your enemies send their regards.",
    "That war? We don't talk about that war.",
    "Last war was an L. Respect to the enemy, I guess.",
    "We lost that one. It happens. Shouldn't, but it does.",
    "Defeat. Time to learn and move on. Mostly move on.",
    "Lost it. The enemy was simply better today. Allegedly.",
    "That war didn't go our way. Next one will. It has to.",
]

async def get_sassy_war_response() -> str:
    """Get a sassy response based on current war status (CWL-aware)"""
    try:
        war_data, is_cwl = await get_active_war_for_chat_cached()
    except Exception:
        return random.choice(RESPONSES_NOT_IN_WAR)

    if not war_data:
        return random.choice(RESPONSES_NOT_IN_WAR)

    state = war_data.get("state")
    cwl_day = war_data.get("_cwl_day") if is_cwl else None

    if state == "notInWar":
        return random.choice(RESPONSES_NOT_IN_WAR)

    if state == "preparation":
        if is_cwl and cwl_day:
            return random.choice(RESPONSES_CWL_PREP).format(day=cwl_day)
        return random.choice(RESPONSES_PREPARATION)

    # Get scores
    clan = war_data.get("clan", {})
    opponent = war_data.get("opponent", {})

    clan_stars = clan.get("stars", 0)
    opponent_stars = opponent.get("stars", 0)
    clan_destruction = clan.get("destructionPercentage", 0)
    opponent_destruction = opponent.get("destructionPercentage", 0)

    star_diff = clan_stars - opponent_stars
    dest_diff = clan_destruction - opponent_destruction

    # War ended - return result
    if state == "warEnded":
        if is_cwl and cwl_day and cwl_day < 7:
            # Mid-CWL day ended (between days)
            if star_diff > 0 or (star_diff == 0 and dest_diff > 0):
                summary = f"We took Day {cwl_day} ({clan_stars}⭐ vs {opponent_stars}⭐)."
            elif star_diff < 0 or (star_diff == 0 and dest_diff < 0):
                summary = f"We dropped Day {cwl_day} ({clan_stars}⭐ vs {opponent_stars}⭐)."
            else:
                summary = f"Day {cwl_day} ended in a draw ({clan_stars}⭐ each)."
            return random.choice(RESPONSES_CWL_DAY_ENDED).format(day=cwl_day, result_summary=summary)

        if star_diff > 0 or (star_diff == 0 and dest_diff > 0):
            return random.choice(RESPONSES_WAR_ENDED_WIN)
        elif star_diff < 0 or (star_diff == 0 and dest_diff < 0):
            return random.choice(RESPONSES_WAR_ENDED_LOSS)
        else:
            return "War ended in a perfect tie. What are the odds?"

    # In war (inWar) — pick CWL-flavored or regular response based on score
    if is_cwl and cwl_day:
        if star_diff == 0:
            if abs(dest_diff) < 5:
                return random.choice(RESPONSES_CWL_TIED).format(day=cwl_day)
            elif dest_diff > 0:
                return random.choice(RESPONSES_CWL_WIN_CLOSE).format(day=cwl_day)
            else:
                return random.choice(RESPONSES_CWL_LOSE_CLOSE).format(day=cwl_day)
        elif star_diff >= 10:
            return random.choice(RESPONSES_CWL_WIN_BIG).format(day=cwl_day)
        elif star_diff >= 3:
            return random.choice(RESPONSES_CWL_WIN_CLOSE).format(day=cwl_day)
        elif star_diff <= -10:
            return random.choice(RESPONSES_CWL_LOSE_BIG).format(day=cwl_day)
        elif star_diff <= -3:
            return random.choice(RESPONSES_CWL_LOSE_CLOSE).format(day=cwl_day)
        elif star_diff > 0:
            return random.choice(RESPONSES_CWL_WIN_CLOSE).format(day=cwl_day)
        else:
            return random.choice(RESPONSES_CWL_LOSE_CLOSE).format(day=cwl_day)

    # Regular war
    if star_diff == 0:
        if abs(dest_diff) < 5:
            return random.choice(RESPONSES_TIED)
        elif dest_diff > 0:
            return random.choice(RESPONSES_WINNING_CLOSE)
        else:
            return random.choice(RESPONSES_LOSING_CLOSE)
    elif star_diff >= 10:
        return random.choice(RESPONSES_WINNING_HARD)
    elif star_diff >= 3:
        return random.choice(RESPONSES_WINNING_CLOSE)
    elif star_diff <= -10:
        return random.choice(RESPONSES_LOSING_HARD)
    elif star_diff <= -3:
        return random.choice(RESPONSES_LOSING_CLOSE)
    elif star_diff > 0:
        return random.choice(RESPONSES_WINNING_CLOSE)
    else:
        return random.choice(RESPONSES_LOSING_CLOSE)

async def get_pushback_response() -> str:
    """Respond to a user pushing back on the bot's war-status answer.

    Re-checks war state and either acknowledges the user is right or
    doubles down that they're wrong.
    """
    try:
        war_data, is_cwl = await get_active_war_for_chat_cached()
    except Exception:
        return random.choice(RESPONSES_PUSHBACK_INWAR_FALSE)

    if not war_data:
        return random.choice(RESPONSES_PUSHBACK_INWAR_FALSE)

    state = war_data.get("state")
    if state in ["preparation", "inWar", "warEnded"]:
        return random.choice(RESPONSES_PUSHBACK_INWAR_TRUE)
    return random.choice(RESPONSES_PUSHBACK_INWAR_FALSE)

def _format_member_link_info(player_tag: str, speaker_account_tags: set) -> str:
    """Return a short ", linked to Discord: X" / ", unlinked" annotation
    for the war-context block, plus a self-flag if the speaker owns this
    account. Best-effort — silent on DB errors."""
    if not player_tag:
        return ""
    try:
        linked_ids = db.get_discord_ids_for_account(player_tag)
    except Exception:
        return ""
    tag_upper = player_tag.upper() if player_tag.startswith("#") else f"#{player_tag.upper()}"
    is_speaker = tag_upper in speaker_account_tags
    if not linked_ids:
        return ", unlinked"
    names = []
    for did in linked_ids:
        try:
            user = db.get_discord_user(did)
            if user and user.get("discord_name"):
                names.append(user["discord_name"])
        except Exception:
            continue
    if names:
        suffix = f", linked to Discord: {', '.join(names)}"
    else:
        suffix = ", linked"
    if is_speaker:
        suffix += " — THIS IS THE SPEAKER'S OWN ACCOUNT"
    return suffix

# Lightweight cache for the chat-side war fetch. War state changes slowly
# (the monitor polls every 2 min). Caching the heavy fetch — which makes
# multiple CoC API calls per chat message — for 60s prevents @-mentions
# from queuing behind 8 HTTP calls when a clan is in CWL.
_chat_war_cache: dict = {"data": None, "is_cwl": False, "ts": 0.0}
_CHAT_WAR_CACHE_TTL = 60.0  # seconds

# Per-player TH/hero cache so we can answer "what about Gunny?" without
# hitting CoC API on every chat message. Player TH changes rarely (an
# upgrade takes weeks), so 1h TTL is plenty conservative.
_player_meta_cache: dict = {}  # player_tag -> (data_dict, ts)
_PLAYER_META_TTL = 3600.0

async def _fetch_player_meta_cached(player_tag: str) -> Optional[dict]:
    """Fetch a player's TH + hero info, cached for 1h. Returns None on error."""
    import time
    if not player_tag:
        return None
    now = time.monotonic()
    cached = _player_meta_cache.get(player_tag)
    if cached and (now - cached[1]) < _PLAYER_META_TTL:
        return cached[0]
    try:
        data = await bot.coc.get_player(player_tag)
    except Exception as e:
        log.debug(f"Player meta fetch failed for {player_tag}: {e}")
        return None
    if data:
        _player_meta_cache[player_tag] = (data, now)
    return data

def _strip_decorations(name: str) -> str:
    """Bare letters+digits, lowercase. For clan-name matching."""
    if not name:
        return ""
    return re.sub(r'[^A-Za-z0-9]', '', name).lower()

async def _resolve_mentioned_clan_members(content: str, message: discord.Message) -> list[dict]:
    """Find clan members referenced in the chat message and resolve their
    linked CoC account info (TH + key hero levels).

    Detects:
      1. Explicit Discord @-mentions (highest signal)
      2. Free-text occurrences of a known clan member's Discord display_name
         OR linked CoC player_name (min 4 chars, whole-word match)

    Returns a list of {discord_name, coc_name, th, heroes_summary}, capped
    at 5 entries to keep the LLM context tight.
    """
    mentioned: list[dict] = []
    seen_tags: set[str] = set()

    async def _add_for_discord_id(discord_id: str, mention_source: str):
        """Resolve a Discord user's linked CoC accounts and append entries."""
        try:
            accounts = db.get_accounts_for_discord_user(str(discord_id))
        except Exception:
            return
        for acc in accounts:
            tag = acc.get("player_tag", "")
            if not tag or tag in seen_tags:
                continue
            seen_tags.add(tag)
            data = await _fetch_player_meta_cached(tag)
            if not data:
                continue
            heroes = [(h.get("name", "?"), h.get("level", 0)) for h in data.get("heroes", [])]
            heroes_summary = ", ".join(f"{n} lvl {l}" for n, l in heroes[:6]) if heroes else "no heroes"
            mentioned.append({
                "mention_source": mention_source,
                "discord_name": mention_source if mention_source.startswith("@") else "",
                "coc_name": data.get("name", acc.get("player_name", "?")),
                "player_tag": tag,
                "th": data.get("townHallLevel"),
                "heroes_summary": heroes_summary,
                "trophies": data.get("trophies"),
            })
            if len(mentioned) >= 5:
                return

    # 1. Explicit Discord @-mentions in the message
    for user in message.mentions:
        if user.bot:
            continue
        await _add_for_discord_id(user.id, f"@{user.name}")
        if len(mentioned) >= 5:
            return mentioned

    # 2. Free-text name matches against known linked clanmates
    try:
        all_links = db.get_all_links()
    except Exception:
        all_links = []

    content_lower = content.lower()
    for link in all_links:
        tag = link.get("player_tag", "")
        if not tag or tag in seen_tags:
            continue
        coc_name = link.get("player_name", "") or ""
        discord_name = link.get("discord_name", "") or ""
        # Try the bare-name "core" of each as a whole-word match
        for raw_name in (coc_name, discord_name):
            core = _strip_decorations(raw_name)
            if len(core) < 4:
                continue
            if re.search(rf'\b{re.escape(core)}\b', content_lower):
                seen_tags.add(tag)
                discord_id = link.get("discord_id", "")
                if discord_id:
                    await _add_for_discord_id(discord_id, raw_name)
                if len(mentioned) >= 5:
                    return mentioned
                break  # don't match the same player twice via different name

    return mentioned

async def get_active_war_for_chat_cached() -> tuple[Optional[dict], bool]:
    """Cached wrapper around get_active_war_for_chat — TTL ~60s."""
    import time
    now = time.monotonic()
    if _chat_war_cache["data"] is not None and (now - _chat_war_cache["ts"]) < _CHAT_WAR_CACHE_TTL:
        return _chat_war_cache["data"], _chat_war_cache["is_cwl"]
    data, is_cwl = await get_active_war_for_chat()
    _chat_war_cache["data"] = data
    _chat_war_cache["is_cwl"] = is_cwl
    _chat_war_cache["ts"] = now
    return data, is_cwl

def _resolve_favorite_member() -> Optional[discord.Member]:
    """Find the configured favorite user in any joined guild. Returns the
    Member object (so we can format a real mention) or None if not found."""
    target = (config.FAVORITE_DISCORD_USERNAME or "").lower()
    if not target:
        return None
    for guild in bot.guilds:
        for member in guild.members:
            if (member.name or "").lower() == target:
                return member
            if (member.display_name or "").lower() == target:
                return member
    return None

async def build_war_context_for_llm(
    speaker_discord_id: str = "",
    mentioned_players: Optional[list[dict]] = None,
) -> str:
    """Compact war-state snapshot injected into the LLM system prompt so the
    model can speak factually without being able to invent stats. Includes
    per-member attack results AND linked-Discord status so name-specific
    questions ("did pr8a attack?") can be answered from data and disambiguated
    via the link table when names are similar (e.g. pr7a alt vs pr8a friend)."""
    try:
        war_data, is_cwl = await get_active_war_for_chat_cached()
    except Exception:
        return "No active war or CWL data available right now."

    if not war_data:
        return "No active war or CWL right now."

    state = war_data.get("state", "unknown")
    clan = war_data.get("clan", {})
    opponent = war_data.get("opponent", {})
    cwl_day = war_data.get("_cwl_day") if is_cwl else None
    team_size = war_data.get("teamSize", 0)

    # Resolve speaker's own linked CoC accounts so we can flag self-queries
    speaker_account_tags = set()
    if speaker_discord_id:
        try:
            for acc in db.get_accounts_for_discord_user(speaker_discord_id):
                tag = acc.get("player_tag", "")
                if tag:
                    speaker_account_tags.add(tag.upper() if tag.startswith("#") else f"#{tag.upper()}")
        except Exception:
            pass

    lines = []
    if is_cwl and cwl_day:
        lines.append(f"Currently in CWL Day {cwl_day} of 7.")
    elif is_cwl:
        lines.append("Currently in CWL.")
    else:
        lines.append("Currently in a regular clan war.")

    lines.append(f"Opponent clan: {opponent.get('name', '?')}")
    lines.append(f"State: {state}")

    if speaker_account_tags:
        lines.append(
            f"The speaker is linked to {len(speaker_account_tags)} CoC account(s) in this clan."
        )

    # Inject the bot's hardcoded favorite — used by the LLM as a positive
    # reference point and (rarely) @-pinged in roasts.
    favorite = _resolve_favorite_member()
    if favorite:
        # Use the bare username (not display_name) so the LLM gets the literal
        # handle to reproduce verbatim. Wrap in backticks as a visual signal
        # that this is a token, not a sentence.
        favorite_handle = favorite.name
        is_self = str(favorite.id) == str(speaker_discord_id)
        if is_self:
            lines.append(
                f"FAVORITE CLAN MEMBER (literal handle, copy verbatim — DO NOT split into words): "
                f"`{favorite_handle}` — and that is exactly who is speaking right now. "
                f"Be warm, defer to them, never roast them."
            )
        else:
            lines.append(
                f"FAVORITE CLAN MEMBER (literal handle, copy verbatim — DO NOT split into words): "
                f"`{favorite_handle}` — write it exactly as shown. Never ping them unprompted. Never confuse them with Gunny."
            )

    if state in ("inWar", "warEnded"):
        lines.append(
            f"Score: {clan.get('stars', 0)} stars, {clan.get('destructionPercentage', 0):.1f}% "
            f"vs opponent {opponent.get('stars', 0)} stars, {opponent.get('destructionPercentage', 0):.1f}%"
        )

        members = clan.get("members", [])
        attacks_per = 1 if is_cwl else 2

        attacked_lines = []
        partial_lines = []
        not_attacked_lines = []
        total_used = 0

        for m in members:
            name = m.get("name", "?")
            tag = m.get("tag", "")
            th = m.get("townhallLevel", "?")
            attacks = m.get("attacks", [])
            used = len(attacks)
            total_used += used
            link_info = _format_member_link_info(tag, speaker_account_tags)

            if used >= attacks_per:
                stars = sum(a.get("stars", 0) for a in attacks)
                dest_avg = sum(a.get("destructionPercentage", 0) for a in attacks) / used
                attacked_lines.append(f"{name} (TH{th}, {stars}★ {dest_avg:.0f}%{link_info})")
            elif used > 0:
                stars = sum(a.get("stars", 0) for a in attacks)
                partial_lines.append(
                    f"{name} (TH{th}, {used}/{attacks_per} done, {stars}★{link_info})"
                )
            else:
                not_attacked_lines.append(f"{name} (TH{th}{link_info})")

        max_attacks = team_size * attacks_per
        lines.append(f"Attacks used: {total_used}/{max_attacks}")

        if attacked_lines:
            lines.append("Players who fully used their attacks: " + ", ".join(attacked_lines))
        if partial_lines:
            lines.append("Players with partial attacks done: " + ", ".join(partial_lines))
        if not_attacked_lines:
            lines.append("Players who have NOT attacked yet: " + ", ".join(not_attacked_lines))

    # MENTIONED PLAYERS — populated when the chat message references a clan
    # member by Discord @-mention or by a known display/CoC name. This is the
    # ONLY authoritative source the LLM has for TH/heroes of clanmates who
    # may not be in the current war roster (e.g. sitting out CWL). The prompt
    # forbids inventing TH levels, so without this block the bot must say it
    # doesn't know.
    if mentioned_players:
        m_lines = []
        for m in mentioned_players:
            th = m.get("th")
            th_str = f"TH{th}" if th else "TH unknown"
            heroes = m.get("heroes_summary", "")
            coc_name = m.get("coc_name", "?")
            discord_name = m.get("discord_name") or m.get("mention_source", "")
            tag = m.get("player_tag", "")
            who = f"{discord_name} (CoC: {coc_name}{', tag ' + tag if tag else ''})" if discord_name else f"CoC: {coc_name}"
            m_lines.append(f"{who} — {th_str}, heroes: {heroes}")
        lines.append("MENTIONED PLAYERS (use this for any TH/hero stats about these players — do not invent any others):")
        for ml in m_lines:
            lines.append(f"  - {ml}")

    return "\n".join(lines)

@bot.event
async def on_message(message: discord.Message):
    """Handle direct mentions with dynamic responses"""
    # Ignore bot's own messages
    if message.author == bot.user:
        return
    
    # Check if bot is mentioned
    if bot.user not in message.mentions:
        await bot.process_commands(message)
        return
    
    # Remove the mention to get the actual message content
    content = message.content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
    
    # Empty message - just a ping
    if not content:
        response = await get_sassy_war_response()
        await message.reply(response, mention_author=False)
        return
    
    chat_ai_enabled = db.get_config("chat_ai_enabled") == "1"

    # Factual paths always stay deterministic — they trigger real API/DB
    # lookups and the user expects accurate info, not creative writing.
    if check_pattern_match(content, USER_IN_WAR_CLAIM_PATTERNS):
        response = await get_pushback_response()
    elif check_pattern_match(content, WAR_STARTED_PATTERNS):
        response = await get_war_started_response()
    elif check_pattern_match(content, WAR_STATUS_PATTERNS):
        response = await get_sassy_war_response()
    # Hard refusal pre-filter — never lets an off-scope ask reach the model
    elif chat_ai_enabled and check_pattern_match(content, OFF_SCOPE_PATTERNS):
        response = random.choice(RESPONSES_OFF_SCOPE)
    elif chat_ai_enabled:
        # Everything else — greetings, insults, compliments, identity asks,
        # general chitchat — goes to the LLM for dynamic, in-character replies.
        llm_reply = None
        try:
            async with message.channel.typing():
                # Resolve clan members referenced in the message so we have
                # authoritative TH/hero data for them (no model speculation).
                mentioned = await _resolve_mentioned_clan_members(content, message)
                war_context = await build_war_context_for_llm(
                    speaker_discord_id=str(message.author.id),
                    mentioned_players=mentioned,
                )
                llm_reply = await llm.generate_chat_response(
                    user_message=content,
                    user_id=str(message.author.id),
                    user_name=message.author.display_name,
                    channel_id=str(message.channel.id),
                    war_context=war_context,
                )
        except Exception as e:
            log.error(f"LLM fallback failed: {e}")

        if llm_reply:
            response = llm_reply
        else:
            # LLM unavailable — degrade gracefully to the old static behavior
            response = await _static_fallback_response(content)
    else:
        # Toggle off — original deterministic behavior across all unmatched cases
        response = await _static_fallback_response(content)

    await message.reply(response, mention_author=False)
    await bot.process_commands(message)

async def _static_fallback_response(content: str) -> str:
    """Pre-LLM behavior. Used when AI is off, or as graceful fallback
    if the LLM is unreachable / times out."""
    if check_pattern_match(content, INSULT_PATTERNS):
        return random.choice(RESPONSES_INSULT)
    if check_pattern_match(content, COMPLIMENT_PATTERNS):
        return random.choice(RESPONSES_COMPLIMENT)
    if check_pattern_match(content, IDENTITY_PATTERNS):
        return random.choice(RESPONSES_IDENTITY)
    if check_pattern_match(content, GREETING_PATTERNS):
        return random.choice(RESPONSES_GREETING)
    if random.random() < 0.3:
        return random.choice(RESPONSES_CONFUSED)
    prefix = random.choice([
        "Not sure what you mean, but here's the war status: ",
        "I'll pretend that was a war question. ",
        "Whatever. Anyway, ",
        "Okay? Anyway, war update: ",
        "Sure. Meanwhile, ",
    ])
    return prefix + await get_sassy_war_response()

# General Commands

@bot.tree.command(name="ping", description="Check if bot is alive")
@app_commands.describe(show="Show message publicly in channel")
async def ping_command(interaction: discord.Interaction, show: bool = False):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency}ms", ephemeral=not show)

@bot.tree.command(name="chat-ai", description="[Beta] Toggle AI conversational fallback")
@app_commands.describe(mode="on, off, or status")
@app_commands.choices(mode=[
    app_commands.Choice(name="on", value="on"),
    app_commands.Choice(name="off", value="off"),
    app_commands.Choice(name="status", value="status"),
])
async def chat_ai_command(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    # Authorization: only the configured beta user.
    # Modern Discord usernames are lowercase, so compare case-insensitively
    # against the bare username (interaction.user.name) and the user ID
    # (BETA_TOGGLE_USER_ID — set via env if you want unambiguous matching).
    expected_name = config.BETA_TOGGLE_USERNAME.lower()
    actual_name = (interaction.user.name or "").lower()
    expected_id = getattr(config, "BETA_TOGGLE_USER_ID", "") or ""
    actual_id = str(interaction.user.id)

    authorized = (actual_name == expected_name) or (expected_id and actual_id == expected_id)
    if not authorized:
        await interaction.response.send_message(
            f"This is a beta toggle. Only the bot owner can flip it. "
            f"(your username: `{interaction.user.name}`, id: `{actual_id}`)",
            ephemeral=True,
        )
        return

    choice = mode.value

    if choice == "status":
        current = db.get_config("chat_ai_enabled") == "1"
        ollama_up = await llm.is_ollama_up()
        model_ready = await llm.is_model_ready() if ollama_up else False
        msg = (
            f"**Chat AI:** {'ON' if current else 'OFF'}\n"
            f"**Ollama service:** {'reachable' if ollama_up else 'unreachable'}\n"
            f"**Model `{config.OLLAMA_MODEL}`:** {'ready' if model_ready else 'not pulled'}"
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    new_val = choice == "on"

    if new_val:
        await interaction.response.defer(ephemeral=True)
        if not await llm.is_ollama_up():
            await interaction.followup.send(
                "Can't reach Ollama service. Won't enable until it's up.\n"
                "Check that the `ollama` container is running.",
                ephemeral=True,
            )
            return
        if not await llm.is_model_ready():
            await interaction.followup.send(
                f"Ollama is up but model `{config.OLLAMA_MODEL}` isn't pulled yet. "
                f"Run `docker exec coc-war-ollama ollama pull {config.OLLAMA_MODEL}` first, "
                f"then try again.",
                ephemeral=True,
            )
            return
        db.set_config("chat_ai_enabled", "1")
        await interaction.followup.send(
            "Chat AI is now **ON** (beta). Affects only the conversational fallback path — "
            "deterministic war/CWL responses are unchanged.",
            ephemeral=True,
        )
    else:
        db.set_config("chat_ai_enabled", "0")
        await interaction.response.send_message(
            "Chat AI is now **OFF**. Falling back to the static response pool.",
            ephemeral=True,
        )

@bot.tree.command(name="help", description="Show all available commands")
@app_commands.describe(show="Show message publicly in channel")
async def help_command(interaction: discord.Interaction, show: bool = False):
    embed = discord.Embed(
        title="War Reminder Bot",
        description="Track clan wars and get reminders for attacks.",
        color=discord.Color.blue()
    )
    
    general = """`/help` - Show this help
`/ping` - Check if bot is online
`/clan` - Show clan info
`/war` - Show current war status
`/cwl` - Show CWL standings
`/tags` - Show clan roster with player tags

All commands are private by default. Add `show:True` to post publicly (e.g. `/war show:True`)"""
    embed.add_field(name="General", value=general, inline=False)

    linking = """`/link <tag>` - Link your CoC account
`/unlink <tag>` - Remove a link
`/myaccounts` - Show your linked accounts
`/whois <tag>` - See who's linked to an account"""
    embed.add_field(name="Account Linking", value=linking, inline=False)

    # Check if user is admin or co-leader
    is_admin = interaction.user.guild_permissions.administrator
    is_coleader = any(role.name.lower() == "co-leader" for role in interaction.user.roles)

    if is_admin or is_coleader:
        admin = """`/forcelink @user <tag>` - Link for someone else
`/forceunlink <tag> [@user]` - Remove links
`/links` - Show all account links
`/cwlstats` - CWL individual performance"""
        embed.add_field(name="Admin", value=admin, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=not show)

@bot.tree.command(name="clan", description="Show clan information")
@app_commands.describe(show="Show message publicly in channel")
async def clan_command(interaction: discord.Interaction, show: bool = False):
    if not await safe_defer(interaction, ephemeral=not show):
        return
    
    try:
        clan_data = await bot.coc.get_clan(config.CLAN_TAG)
    except CoCAPIError as e:
        await interaction.followup.send(f"API Error: {e.message}")
        return
    
    embed = discord.Embed(
        title=clan_data.get('name'),
        description=clan_data.get("description", ""),
        color=discord.Color.gold()
    )
    
    embed.add_field(name="Tag", value=clan_data.get("tag"), inline=True)
    embed.add_field(name="Trophies", value=f"{clan_data.get('clanPoints', 0):,}", inline=True)
    embed.add_field(name="Members", value=f"{clan_data.get('members', 0)}/50", inline=True)
    embed.add_field(name="War League", value=clan_data.get("warLeague", {}).get("name", "Unknown"), inline=True)
    embed.add_field(name="Win Streak", value=clan_data.get("warWinStreak", 0), inline=True)
    embed.add_field(name="Record", value=f"W: {clan_data.get('warWins', 0)} | L: {clan_data.get('warLosses', 'N/A')} | D: {clan_data.get('warTies', 'N/A')}", inline=True)
    
    if clan_data.get("badgeUrls", {}).get("medium"):
        embed.set_thumbnail(url=clan_data["badgeUrls"]["medium"])
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="war", description="Show current war status")
@app_commands.describe(show="Show message publicly in channel")
async def war_command(interaction: discord.Interaction, show: bool = False):
    if not await safe_defer(interaction, ephemeral=not show):
        return
    
    war_data = None
    is_cwl = False
    
    # Try regular war first
    try:
        war_data = await bot.coc.get_current_war(config.CLAN_TAG)
        state = war_data.get("state")
        
        # If not in regular war, try CWL
        if state == "notInWar":
            war_data = None
    except CoCAPIError as e:
        # 403 = private war log (common during CWL), try CWL
        if e.status not in [403, 404]:
            await interaction.followup.send(f"API Error: {e.message}")
            return
    
    # Try CWL if no regular war
    if not war_data:
        try:
            league_group = await bot.coc.get_cwl_group(config.CLAN_TAG)
            if league_group:
                our_tag = config.CLAN_TAG.upper()
                if not our_tag.startswith("#"):
                    our_tag = f"#{our_tag}"
                
                rounds = league_group.get("rounds", [])
                
                # Find active CWL war (prefer inWar, then preparation)
                for round_data in rounds:
                    war_tags = round_data.get("warTags", [])
                    for war_tag in war_tags:
                        if war_tag == "#0":
                            continue
                        try:
                            cwl_war = await bot.coc.get_cwl_war(war_tag)
                            clan = cwl_war.get("clan", {})
                            opponent = cwl_war.get("opponent", {})
                            
                            # Check if we're in this war
                            if clan.get("tag", "").upper() == our_tag or opponent.get("tag", "").upper() == our_tag:
                                state = cwl_war.get("state")
                                if state in ["inWar", "preparation"]:
                                    # Swap if we're the opponent
                                    if opponent.get("tag", "").upper() == our_tag:
                                        cwl_war["clan"], cwl_war["opponent"] = cwl_war["opponent"], cwl_war["clan"]
                                    war_data = cwl_war
                                    is_cwl = True
                                    break
                        except:
                            continue
                    if war_data:
                        break
        except CoCAPIError:
            pass
    
    if not war_data:
        await interaction.followup.send("Not currently in a war.")
        return
    
    state = war_data.get("state")
    clan = war_data.get("clan", {})
    opponent = war_data.get("opponent", {})
    team_size = war_data.get("teamSize", 0)
    attacks_per_member = 1 if is_cwl else 2
    
    war_type = "CWL" if is_cwl else "War"
    
    embed = discord.Embed(
        title=f"⚔️ {clan.get('name')} vs {opponent.get('name')}",
        description=f"**{war_type}** • {team_size}v{team_size}",
        color=get_state_color(state)
    )
    
    if state == "preparation":
        start_time = parse_coc_timestamp(war_data.get("startTime"))
        embed.add_field(name="Status", value=f"Preparation Day\nBattle starts in: **{format_time_remaining(start_time)}**", inline=False)
    elif state == "inWar":
        end_time = parse_coc_timestamp(war_data.get("endTime"))
        embed.add_field(name="Status", value=f"Battle Day\nEnds in: **{format_time_remaining(end_time)}**", inline=False)
    elif state == "warEnded":
        embed.add_field(name="Status", value="War Ended", inline=False)
    
    embed.add_field(
        name=clan.get('name'),
        value=f"⭐ {clan.get('stars', 0)} | {clan.get('destructionPercentage', 0):.1f}%",
        inline=True
    )
    embed.add_field(
        name=opponent.get('name'),
        value=f"⭐ {opponent.get('stars', 0)} | {opponent.get('destructionPercentage', 0):.1f}%",
        inline=True
    )
    
    clan_members = sorted(clan.get("members", []), key=lambda m: m.get("mapPosition", 99))
    
    roster_lines = []
    for member in clan_members:
        name = member.get("name", "Unknown")
        th = member.get("townhallLevel", "?")
        pos = member.get("mapPosition", "?")
        
        if state in ["inWar", "warEnded"]:
            attacks = member.get("attacks", [])
            used = len(attacks)
            total = attacks_per_member
            stars = sum(a.get("stars", 0) for a in attacks)
            dest = sum(a.get("destructionPercentage", 0) for a in attacks)
            
            if used > 0:
                roster_lines.append(f"`{pos:>2}.` **{name}** (TH{th}) - {used}/{total} | ⭐{stars} | {dest:.0f}%")
            else:
                roster_lines.append(f"`{pos:>2}.` **{name}** (TH{th}) - 0/{total}")
        else:
            roster_lines.append(f"`{pos:>2}.` **{name}** (TH{th})")
    
    chunks = []
    current = []
    current_len = 0
    
    for line in roster_lines:
        if current_len + len(line) + 1 > 1000:
            chunks.append("\n".join(current))
            current = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line) + 1
    
    if current:
        chunks.append("\n".join(current))
    
    for i, chunk in enumerate(chunks):
        name = "Roster" if i == 0 else "Roster (cont.)"
        embed.add_field(name=name, value=chunk, inline=False)
    
    if state in ["inWar", "warEnded"]:
        total_attacks = sum(len(m.get("attacks", [])) for m in clan_members)
        max_attacks = team_size * attacks_per_member
        
        # Enemy attacks
        enemy_members = opponent.get("members", [])
        enemy_attacks = sum(len(m.get("attacks", [])) for m in enemy_members)
        
        no_attacks = [m.get("name") for m in clan_members if len(m.get("attacks", [])) == 0]
        one_attack = [m.get("name") for m in clan_members if len(m.get("attacks", [])) == 1 and attacks_per_member > 1]
        
        summary = f"**{total_attacks}/{max_attacks}** attacks used (enemy: {enemy_attacks}/{max_attacks})"
        if no_attacks:
            summary += f"\n0 attacks: {', '.join(no_attacks)}"
        if one_attack:
            summary += f"\n1 attack: {', '.join(one_attack)}"
        
        embed.add_field(name="Attacks", value=summary, inline=False)
    
    await interaction.followup.send(embed=embed)

# Linking Commands

@bot.tree.command(name="link", description="Link your Discord to a CoC account")
@app_commands.describe(player_tag="Your player tag (e.g. #ABC123)", show="Show message publicly in channel")
async def link_command(interaction: discord.Interaction, player_tag: str, show: bool = False):
    if not await safe_defer(interaction, ephemeral=not show):
        return
    
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    try:
        player_data = await bot.coc.get_player(player_tag)
    except CoCAPIError as e:
        if e.status == 404:
            await interaction.followup.send(f"Player not found: `{player_tag}`")
        else:
            await interaction.followup.send(f"API Error: {e.message}")
        return
    
    player_name = player_data.get("name", "Unknown")
    
    db.upsert_discord_user(str(interaction.user.id), interaction.user.display_name)
    db.upsert_coc_account(player_tag, player_name)
    created = db.link_account(player_tag, str(interaction.user.id), str(interaction.user.id))
    
    if created:
        log.info(f"{interaction.user.name} linked to {player_name} ({player_tag})")
        await interaction.followup.send(f"Linked **{player_name}** (`{player_tag}`) to your account.")
    else:
        await interaction.followup.send(f"Already linked to **{player_name}** (`{player_tag}`).")

@bot.tree.command(name="unlink", description="Unlink a CoC account from your Discord")
@app_commands.describe(player_tag="The player tag to unlink", show="Show message publicly in channel")
async def unlink_command(interaction: discord.Interaction, player_tag: str, show: bool = False):
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()

    removed = db.unlink_account(player_tag, str(interaction.user.id))

    if removed:
        log.info(f"{interaction.user.name} unlinked from {player_tag}")
        await interaction.response.send_message(f"Unlinked `{player_tag}` from your account.", ephemeral=not show)
    else:
        await interaction.response.send_message(f"You don't have `{player_tag}` linked.", ephemeral=not show)

@bot.tree.command(name="myaccounts", description="Show your linked CoC accounts")
@app_commands.describe(show="Show message publicly in channel")
async def myaccounts_command(interaction: discord.Interaction, show: bool = False):
    accounts = db.get_accounts_for_discord_user(str(interaction.user.id))

    if not accounts:
        await interaction.response.send_message("No linked accounts. Use `/link #YourTag` to link.", ephemeral=not show)
        return

    lines = [f"**{a['player_name']}** `{a['player_tag']}`" for a in accounts]

    embed = discord.Embed(
        title=f"{interaction.user.display_name}'s Accounts",
        description="\n".join(lines),
        color=discord.Color.blue()
    )

    await interaction.response.send_message(embed=embed, ephemeral=not show)

@bot.tree.command(name="whois", description="Show who is linked to a CoC account")
@app_commands.describe(player_tag="The player tag to look up", show="Show message publicly in channel")
async def whois_command(interaction: discord.Interaction, player_tag: str, show: bool = False):
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()

    account = db.get_coc_account(player_tag)
    if not account:
        await interaction.response.send_message(f"No account found for `{player_tag}`", ephemeral=not show)
        return

    discord_ids = db.get_discord_ids_for_account(player_tag)

    if not discord_ids:
        await interaction.response.send_message(f"**{account['player_name']}** `{player_tag}` - not linked", ephemeral=not show)
        return

    mentions = [f"<@{did}>" for did in discord_ids]
    await interaction.response.send_message(f"**{account['player_name']}** `{player_tag}` → {', '.join(mentions)}", ephemeral=not show)

# Admin Commands

@bot.tree.command(name="forcelink", description="[Admin] Link a CoC account to a Discord user")
@app_commands.describe(user="The Discord user", player_tag="The player tag", show="Show message publicly in channel")
@is_admin_or_coleader()
async def forcelink_command(interaction: discord.Interaction, user: discord.Member, player_tag: str, show: bool = False):
    if not await safe_defer(interaction, ephemeral=not show):
        return
    
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()
    
    try:
        player_data = await bot.coc.get_player(player_tag)
    except CoCAPIError as e:
        if e.status == 404:
            await interaction.followup.send(f"Player not found: `{player_tag}`")
        else:
            await interaction.followup.send(f"API Error: {e.message}")
        return
    
    player_name = player_data.get("name", "Unknown")
    
    db.upsert_discord_user(str(user.id), user.display_name)
    db.upsert_coc_account(player_tag, player_name)
    created = db.link_account(player_tag, str(user.id), str(interaction.user.id))
    
    if created:
        log.info(f"{interaction.user.name} force-linked {user.name} to {player_name} ({player_tag})")
        await interaction.followup.send(f"Linked **{player_name}** `{player_tag}` to {user.mention}")
    else:
        await interaction.followup.send(f"{user.mention} already linked to **{player_name}**")

@bot.tree.command(name="forceunlink", description="[Admin] Unlink a CoC account")
@app_commands.describe(player_tag="The player tag", user="Specific user to unlink (optional)", show="Show message publicly in channel")
@is_admin_or_coleader()
async def forceunlink_command(interaction: discord.Interaction, player_tag: str, user: discord.Member = None, show: bool = False):
    if not player_tag.startswith("#"):
        player_tag = f"#{player_tag}"
    player_tag = player_tag.upper()

    if user:
        removed = db.unlink_account(player_tag, str(user.id))
        if removed:
            log.info(f"{interaction.user.name} force-unlinked {user.name} from {player_tag}")
            await interaction.response.send_message(f"Unlinked `{player_tag}` from {user.mention}", ephemeral=not show)
        else:
            await interaction.response.send_message(f"{user.mention} wasn't linked to `{player_tag}`", ephemeral=not show)
    else:
        count = db.unlink_all_from_account(player_tag)
        if count > 0:
            log.info(f"{interaction.user.name} force-unlinked all ({count}) from {player_tag}")
            await interaction.response.send_message(f"Removed {count} link(s) from `{player_tag}`", ephemeral=not show)
        else:
            await interaction.response.send_message(f"No links found for `{player_tag}`", ephemeral=not show)

@bot.tree.command(name="links", description="[Admin] Show all account links")
@app_commands.describe(show="Show message publicly in channel")
@is_admin_or_coleader()
async def links_command(interaction: discord.Interaction, show: bool = False):
    all_links = db.get_all_links()
    
    if not all_links:
        await interaction.response.send_message("No account links in database.", ephemeral=not show)
        return
    
    by_player = defaultdict(list)
    for link in all_links:
        key = (link['player_tag'], link['player_name'])
        by_player[key].append(link['discord_name'])
    
    lines = [f"**{name}** `{tag}` → {', '.join(users)}" for (tag, name), users in by_player.items()]
    content = "\n".join(lines)
    
    embed = discord.Embed(title="All Account Links", color=discord.Color.blue())
    
    if len(content) <= 4000:
        embed.description = content
    else:
        embed.description = content[:4000]
    
    await interaction.response.send_message(embed=embed, ephemeral=not show)

@bot.tree.command(name="tags", description="Shows clan roster with player tags")
@app_commands.describe(show="Show message publicly in channel")
async def tags_command(interaction: discord.Interaction, show: bool = False):
    if not await safe_defer(interaction, ephemeral=not show):
        return
    
    try:
        clan_data = await bot.coc.get_clan(config.CLAN_TAG)
    except CoCAPIError as e:
        await interaction.followup.send(f"API Error: {e.message}")
        return
    
    members = clan_data.get("memberList", [])
    added = 0
    unlinked = []
    
    for member in members:
        tag = member.get("tag")
        name = member.get("name")
        
        existing = db.get_coc_account(tag)
        db.upsert_coc_account(tag, name)
        if not existing:
            added += 1
        
        discord_ids = db.get_discord_ids_for_account(tag)
        if not discord_ids:
            unlinked.append(f"**{name}** `{tag}`")
    
    embed = discord.Embed(
        title=f"Synced {clan_data.get('name')}",
        color=discord.Color.green()
    )
    
    embed.add_field(
        name="Stats",
        value=f"Total: {len(members)} | New: {added} | Unlinked: {len(unlinked)}",
        inline=False
    )
    
    if unlinked:
        unlinked_text = "\n".join(unlinked)
        
        if len(unlinked_text) <= 1024:
            embed.add_field(name="Unlinked Accounts", value=unlinked_text, inline=False)
        else:
            chunks = []
            current = []
            current_len = 0
            
            for line in unlinked:
                if current_len + len(line) + 1 > 1000:
                    chunks.append("\n".join(current))
                    current = [line]
                    current_len = len(line)
                else:
                    current.append(line)
                    current_len += len(line) + 1
            
            if current:
                chunks.append("\n".join(current))
            
            for i, chunk in enumerate(chunks):
                name = "Unlinked Accounts" if i == 0 else "Unlinked (cont.)"
                embed.add_field(name=name, value=chunk, inline=False)
    else:
        embed.add_field(name="Status", value="All members linked!", inline=False)
    
    log.info(f"Synced {len(members)} members, {len(unlinked)} unlinked")
    await interaction.followup.send(embed=embed)

# CWL Commands

@bot.tree.command(name="cwl", description="Show CWL status and standings")
@app_commands.describe(show="Show message publicly in channel")
async def cwl_command(interaction: discord.Interaction, show: bool = False):
    if not await safe_defer(interaction, ephemeral=not show):
        return
    
    # Try to get CWL data
    try:
        league_group = await bot.coc.get_cwl_group(config.CLAN_TAG)
    except CoCAPIError as e:
        if e.status == 404:
            await interaction.followup.send("Not currently in CWL, or CWL hasn't started yet.")
        else:
            await interaction.followup.send(f"API Error: {e.message}")
        return
    
    if not league_group:
        await interaction.followup.send("No CWL data available.")
        return
    
    # Get our clan tag
    our_tag = config.CLAN_TAG.upper()
    if not our_tag.startswith("#"):
        our_tag = f"#{our_tag}"
    
    clans = league_group.get("clans", [])
    rounds = league_group.get("rounds", [])
    
    # Initialize clan stats
    clan_stats = {}
    for clan in clans:
        clan_stats[clan.get("tag")] = {
            "name": clan.get("name"),
            "stars": 0,
            "destruction": 0.0,
            "wins": 0
        }
    
    # Find current day and our current war
    current_day = 0
    current_war_info = None
    
    # Process each round to find current day and collect stats
    for round_num, round_data in enumerate(rounds, 1):
        war_tags = round_data.get("warTags", [])
        
        # Skip rounds that haven't started
        if not war_tags or war_tags[0] == "#0":
            continue
        
        round_has_inwar = False
        
        for war_tag in war_tags:
            if war_tag == "#0":
                continue
            
            try:
                war = await bot.coc.get_cwl_war(war_tag)
                state = war.get("state")
                
                war_clan = war.get("clan", {})
                war_opponent = war.get("opponent", {})
                clan_tag_1 = war_clan.get("tag")
                clan_tag_2 = war_opponent.get("tag")
                
                # Determine current day from war states
                if state == "inWar":
                    current_day = round_num
                    round_has_inwar = True
                elif state == "preparation" and current_day < round_num:
                    # Prep day means this round hasn't started battle yet
                    pass
                elif state == "warEnded" and current_day == 0:
                    current_day = round_num
                
                # Check if this is our active war
                is_our_war = clan_tag_1.upper() == our_tag or clan_tag_2.upper() == our_tag
                if is_our_war and state == "inWar":
                    # Ensure we're "clan" not "opponent"
                    if clan_tag_2.upper() == our_tag:
                        war_clan, war_opponent = war_opponent, war_clan
                    
                    current_war_info = {
                        "opponent": war_opponent.get("name"),
                        "our_stars": war_clan.get("stars", 0),
                        "their_stars": war_opponent.get("stars", 0),
                        "our_attacks": sum(1 for m in war_clan.get("members", []) if m.get("attacks")),
                        "total_attacks": war.get("teamSize", 15),
                        "our_destruction": war_clan.get("destructionPercentage", 0),
                        "their_destruction": war_opponent.get("destructionPercentage", 0)
                    }
                
                # Collect stats from ended wars AND active wars
                if state in ["inWar", "warEnded"]:
                    if clan_tag_1 in clan_stats:
                        clan_stats[clan_tag_1]["stars"] += war_clan.get("stars", 0)
                        clan_stats[clan_tag_1]["destruction"] += war_clan.get("destructionPercentage", 0)
                    
                    if clan_tag_2 in clan_stats:
                        clan_stats[clan_tag_2]["stars"] += war_opponent.get("stars", 0)
                        clan_stats[clan_tag_2]["destruction"] += war_opponent.get("destructionPercentage", 0)
                    
                    # Only count wins for ended wars
                    if state == "warEnded":
                        stars_1 = war_clan.get("stars", 0)
                        stars_2 = war_opponent.get("stars", 0)
                        dest_1 = war_clan.get("destructionPercentage", 0)
                        dest_2 = war_opponent.get("destructionPercentage", 0)
                        
                        if stars_1 > stars_2 or (stars_1 == stars_2 and dest_1 > dest_2):
                            if clan_tag_1 in clan_stats:
                                clan_stats[clan_tag_1]["wins"] += 1
                        elif stars_2 > stars_1 or (stars_1 == stars_2 and dest_2 > dest_1):
                            if clan_tag_2 in clan_stats:
                                clan_stats[clan_tag_2]["wins"] += 1
                
            except Exception as e:
                print(f"[CWL] Error fetching war {war_tag}: {e}")
                continue
    
    if current_day == 0:
        current_day = 1
    
    # Build standings list and sort
    standings = []
    for tag, stats in clan_stats.items():
        standings.append({
            "tag": tag,
            "name": stats["name"],
            "stars": stats["stars"],
            "destruction": stats["destruction"],
            "wins": stats["wins"]
        })
    
    # Sort by wins * 10 + stars, then destruction
    standings.sort(key=lambda x: (x["wins"] * 10 + x["stars"], x["destruction"]), reverse=True)
    
    embed = discord.Embed(
        title=f"CWL Standings - Day {current_day}/7",
        color=discord.Color.purple()
    )
    
    # Current war status if active
    if current_war_info:
        star_diff = current_war_info['our_stars'] - current_war_info['their_stars']
        if star_diff > 0:
            status_emoji = "🟢"
        elif star_diff < 0:
            status_emoji = "🔴"
        else:
            status_emoji = "🟡"
        
        status = f"{status_emoji} vs **{current_war_info['opponent']}**\n\n"
        status += f"Us: {current_war_info['our_stars']}★ {current_war_info['our_destruction']:.1f}% ({current_war_info['our_attacks']}/{current_war_info['total_attacks']})\n"
        status += f"Them: {current_war_info['their_stars']}★ {current_war_info['their_destruction']:.1f}%"
        embed.add_field(name="Current War", value=status, inline=False)
    
    # Build standings with pipe separators
    lines = "```\n"
    for i, s in enumerate(standings, 1):
        is_us = s["tag"].upper() == our_tag
        marker = "►" if is_us else " "
        name = s["name"][:12]
        lines += f"{marker}{i}│{s['wins']}W│{s['stars']:>2}★│{s['destruction']:>2.0f}%│{name}\n"
    lines += "```"
    
    embed.add_field(name="Standings", value=lines, inline=False)
    
    await interaction.followup.send(embed=embed)

@bot.tree.command(name="cwlstats", description="[Admin] Show CWL individual performance")
@app_commands.describe(show="Show message publicly in channel")
@is_admin_or_coleader()
async def cwlstats_command(interaction: discord.Interaction, show: bool = False):
    if not await safe_defer(interaction, ephemeral=not show):
        return
    
    # Try to get CWL data
    try:
        league_group = await bot.coc.get_cwl_group(config.CLAN_TAG)
    except CoCAPIError as e:
        if e.status == 404:
            await interaction.followup.send("Not currently in CWL.")
        else:
            await interaction.followup.send(f"API Error: {e.message}")
        return
    
    if not league_group:
        await interaction.followup.send("No CWL data available.")
        return
    
    # Get our clan tag
    our_tag = config.CLAN_TAG.upper()
    if not our_tag.startswith("#"):
        our_tag = f"#{our_tag}"
    
    season = league_group.get("season", "")
    rounds = league_group.get("rounds", [])
    
    # Collect player stats from all wars (live from API)
    player_stats = {}  # tag -> {name, stars, destruction, attacks, three_stars, days}
    current_day = 0
    
    for round_num, round_data in enumerate(rounds, 1):
        war_tags = round_data.get("warTags", [])
        if not war_tags or war_tags[0] == "#0":
            continue
        
        for war_tag in war_tags:
            if war_tag == "#0":
                continue
            
            try:
                war = await bot.coc.get_cwl_war(war_tag)
                state = war.get("state")
                
                # Only count inWar and warEnded
                if state not in ["inWar", "warEnded"]:
                    continue
                
                war_clan = war.get("clan", {})
                war_opponent = war.get("opponent", {})
                
                # Check if we're in this war
                if war_clan.get("tag", "").upper() == our_tag:
                    our_members = war_clan.get("members", [])
                elif war_opponent.get("tag", "").upper() == our_tag:
                    our_members = war_opponent.get("members", [])
                else:
                    continue
                
                current_day = max(current_day, round_num)
                
                # Collect stats for each member
                for member in our_members:
                    tag = member.get("tag")
                    name = member.get("name")
                    attacks = member.get("attacks", [])
                    
                    if tag not in player_stats:
                        player_stats[tag] = {
                            "name": name,
                            "stars": 0,
                            "destruction": 0,
                            "attacks": 0,
                            "three_stars": 0,
                            "days": 0
                        }
                    
                    player_stats[tag]["days"] += 1
                    
                    for attack in attacks:
                        stars = attack.get("stars", 0)
                        dest = attack.get("destructionPercentage", 0)
                        player_stats[tag]["stars"] += stars
                        player_stats[tag]["destruction"] += dest
                        player_stats[tag]["attacks"] += 1
                        if stars == 3:
                            player_stats[tag]["three_stars"] += 1
            except:
                continue
    
    if not player_stats:
        await interaction.followup.send("No CWL data found. Make sure CWL has started and at least one war is in progress or completed.")
        return
    
    # Convert to list and sort by stars, then destruction
    performance = []
    for tag, stats in player_stats.items():
        avg_dest = stats["destruction"] / stats["attacks"] if stats["attacks"] > 0 else 0
        performance.append({
            "player_tag": tag,
            "player_name": stats["name"],
            "total_stars": stats["stars"],
            "avg_destruction": avg_dest,
            "attacks_used": stats["attacks"],
            "days_in_roster": stats["days"],
            "three_stars": stats["three_stars"]
        })
    
    performance.sort(key=lambda x: (x["total_stars"], x["avg_destruction"]), reverse=True)
    
    embed = discord.Embed(
        title=f"CWL Individual Performance - Day {current_day}/7",
        description=f"Season: {season}",
        color=discord.Color.purple()
    )
    
    # Top performers
    top_lines = []
    for i, p in enumerate(performance[:15], 1):
        stars = p.get("total_stars", 0)
        avg_dest = p.get("avg_destruction", 0)
        attacks = p.get("attacks_used", 0)
        days = p.get("days_in_roster", 0)
        three_stars = p.get("three_stars", 0)
        
        line = f"`{i:>2}.` **{p['player_name']}** - {stars}★ ({three_stars}×3★) | {avg_dest:.0f}% | {attacks}/{days}"
        top_lines.append(line)
    
    embed.add_field(
        name="Performance (Stars | Avg% | Attacks)",
        value="\n".join(top_lines) if top_lines else "No data",
        inline=False
    )
    
    # Missed attacks
    missed = [p for p in performance if p.get("attacks_used", 0) < p.get("days_in_roster", 0)]
    if missed:
        missed_lines = [f"{p['player_name']} ({p.get('attacks_used', 0)}/{p.get('days_in_roster', 0)})" for p in missed[:10]]
        embed.add_field(
            name="Missed Attacks",
            value=", ".join(missed_lines),
            inline=False
        )
    
    await interaction.followup.send(embed=embed)

# Main

if __name__ == "__main__":
    if not config.BOT_TOKEN:
        log.error("BOT_TOKEN not found in .env")
        exit(1)
    
    if not config.COC_API_KEY:
        log.error("API_KEY not found in .env")
        exit(1)
    
    log.info(f"Starting bot for clan {config.CLAN_TAG}")
    bot.run(config.BOT_TOKEN, log_handler=None)