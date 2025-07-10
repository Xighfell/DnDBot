import discord
from openai import OpenAI
from dotenv import load_dotenv
import os
import asyncio
import json
import re
from collections import defaultdict, deque
import random
from collections import deque



import spacy
nlp = spacy.load("en_core_web_sm")

def parse_action(text):
    doc = nlp(text)
    verb = None
    dobj = None
    for token in doc:
        if token.pos_ == "VERB" and not verb:
            verb = token.lemma_.lower()
        if token.dep_ == "dobj" and not dobj:
            dobj = token.text.lower()
    return verb, dobj

async def classify_action(verb, sentence):
    messages = [
        {"role":"system",
         "content":(
             "You’re a D&D 5e rules assistant. "
             "Classify player verbs into one of: Attack, Athletics, Acrobatics, Stealth, Investigation, Persuasion, Perception."
         )
        },
        {"role":"user",
         "content":(
             f"Given this verb: '{verb}' in the context: '{sentence}', "
             "which of the above categories does it best belong to? "
             "Just reply with the category name."
         )
        }
    ]

    resp = await openai_client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.0,
        max_tokens=4
    )
    return resp.choices[0].message.content.strip()


# Load environment variables
load_dotenv()
openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
discord_token = os.getenv("DISCORD_BOT_TOKEN")

# Load races and classes databases
with open("races.json", "r") as f:
    races_db = json.load(f)

with open("classes.json", "r") as f:
    classes_db = json.load(f)

# Load or initialize player data
try:
    with open("players.json", "r") as f:
        player_data = json.load(f)
except FileNotFoundError:
    player_data = {}
# Load or initialize flavor data for actions (skills, etc.)
try:
    with open("actions.json", "r", encoding="utf-8") as f:
        flavor_data = json.load(f)
except FileNotFoundError:
    flavor_data = {}
    print("Warning: actions.json file not found. Flavor data will be empty.")
ACTION_VERBS = [re.escape(key) for key in flavor_data.keys()]
VERB_PATTERN = rf"\b({'|'.join(ACTION_VERBS)})\b"
monsters_by_channel = defaultdict(dict)

def save_player_data():
    with open("players.json", "w") as f:
        json.dump(player_data, f, indent=2)

async def should_roll_for_action(action_text):
    print("[DEBUG] should_roll_for_action() called with:", action_text)
    try:
        system_prompt = {
            "role": "system",
            "content": (
                "You are a Dungeons & Dragons 5e rules assistant. "
                "Given a player action, decide if it would reasonably require a skill check or dice roll "
                "based on its difficulty, uncertainty, or consequence. "
                "Only reply with 'Yes' if the action is challenging or contested. "
                "Reply with 'No' if it is trivial, automatic, or clearly succeeds without a roll."
            )
        }

        user_prompt = {
            "role": "user",
            "content": f"Should this action require a roll in D&D 5e? Action: '{action_text}'"
        }

        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[system_prompt, user_prompt],
            temperature=0,
            max_tokens=5
        )
        decision = response.choices[0].message.content.strip().lower()
        print(f"[DEBUG] should_roll_for_action decision: {decision}")
        return "yes" in decision

    except Exception as e:
        print(f"Error in should_roll_for_action: {e}")
        return False

async def find_action_key_with_ai(action_text):
    messages = [
        {
            "role": "system",
            "content": "You're a D&D bot that classifies player actions into known categories."
        },
        {
            "role": "user",
            "content": (
                f"Given this player action: '{action_text}', match it to one of the following action types: "
                + ", ".join(flavor_data.keys()) +
                ". Just reply with the best matching key."
            )
        }
    ]

    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            temperature=0.2,
            max_tokens=10
        )

        match = response.choices[0].message.content.strip().lower()
        return match if match in flavor_data else None

    except Exception as e:
        print(f"AI match error: {e}")
        return None


def resolve_player_name(input_name):
    input_name = input_name.strip().lower()
    for stored_name, data in player_data.items():
        if stored_name.lower() == input_name:
            return stored_name
        for alias in data.get("aliases", []):
            if alias.strip().lower() == input_name:
                return stored_name
    return None

def format_player_stats(name):
    data = player_data.get(name)
    if not data:
        return None
    lines = [
        f"**{data.get('name', name).capitalize()}** ({data.get('race', 'Unknown Race')} {data.get('class', 'Unknown Class')})",
        f"**HP:** {data.get('current_hp', '?')}/{data.get('hp', '?')} | **AC:** {data.get('ac', '?')}",
        f"**DEX:** {data.get('dex', '?')} | **DEX Mod:** {data.get('dex_mod', '?')} | **Prof Bonus:** {data.get('proficiency_bonus', '?')}"
    ]
    if data.get("weapons"):
        lines.append("**Weapons:** " + ", ".join(data["weapons"]))
    if data.get("features"):
        lines.append("**Features:**")
        for feat in data["features"]:
            lines.append(f"- {feat}")
    return "\n".join(lines)

def get_player_context(message_content):
    context_strings = []
    lower_msg = message_content.lower()
    for name, stats in player_data.items():
        if name.lower() in lower_msg:
            # Only string or int values for context to keep it concise
            stats_str = ", ".join(f"{k.capitalize()}={v}" for k, v in stats.items() if isinstance(v, (str, int)))
            context_strings.append(f"{stats.get('name', name).capitalize()}: {stats_str}")
    return "\n".join(context_strings)

# Dice roll regex supports expressions like 2d6+3, d20, d10-1, d20+prof, etc.
DICE_ROLL_PATTERN = re.compile(r'(?P<num>\d*)d(?P<sides>\d+)(?P<mod>([+-]\d+|(\+prof))?)')

def roll_dice(dice_expr, player=None):
    match = DICE_ROLL_PATTERN.fullmatch(dice_expr.strip().lower())
    if not match:
        return None, "Invalid dice expression."

    num = int(match.group('num')) if match.group('num') else 1
    sides = int(match.group('sides'))
    mod = match.group('mod')

    rolls = [random.randint(1, sides) for _ in range(num)]
    total = sum(rolls)
    mod_value = 0
    prof_added = False

    if mod:
        if mod == '+prof' and player:
            prof_bonus = player.get("proficiency_bonus", 0)
            mod_value = prof_bonus
            prof_added = True
        else:
            try:
                mod_value = int(mod)
            except:
                mod_value = 0

    total += mod_value
    detail = f"Rolls: {rolls} | Modifier: {mod_value}{' (proficiency bonus)' if prof_added else ''} | Total: {total}"
    return total, detail

# Track ongoing roll prompts per channel: {channel_id: {'player': str, 'reason': str}}
roll_prompts = {}

# Conversation and turn order tracking
intents = discord.Intents.default()
intents.message_content = True
discord_client = discord.Client(intents=intents)

conversation_histories = defaultdict(list)
turn_order = defaultdict(deque)

# System prompt for AI DM
system_prompt = {
    "role": "system",
    "content": (
        "You are a GPT-4-powered Dungeon Master assistant running a dark horror-themed D&D 5e campaign. "
        "Use eerie, mysterious, and immersive gothic descriptions. "
        "Keep responses concise and focused, about 3-5 sentences. Avoid repeating phrases or overly detailed narration."
    )
}

# Autofill race and class details when adding a player
def autofill_player_info(race, char_class):
    race_info = races_db.get(race.lower(), {})
    class_info = classes_db.get(char_class.lower(), {})

    base_hp = class_info.get("base_hp", 10)
    proficiency_bonus = class_info.get("proficiency_bonus", 2)
    features = race_info.get("traits", []) + class_info.get("features", [])

    return base_hp, proficiency_bonus, features

@discord_client.event
async def on_ready():
    print(f'Logged in as {discord_client.user}')

@discord_client.event
async def on_message(message):
    if message.author == discord_client.user:
        return

    content = " ".join(message.content.strip().split())
    parts = content.split()
    if not parts:
        return

    command = parts[0].lower()
    args = parts[1:]
    channel_id = message.channel.id

    # Handle ongoing roll prompt
    if channel_id in roll_prompts:
        roll_info = roll_prompts[channel_id]
        if command == "!roll":
            if len(args) < 2:
                await message.channel.send("Usage: `!roll [player] [dice_expression]`")
                return
            roll_player = resolve_player_name(args[0])
            if roll_player != roll_info['player']:
                await message.channel.send(f"It's {roll_info['player'].capitalize()}'s turn to roll.")
                return
            dice_expr = " ".join(args[1:])
            player = player_data.get(roll_player, {})
            total, detail = roll_dice(dice_expr, player)
            if total is None:
                await message.channel.send(f"Invalid dice expression: `{dice_expr}`")
                return
            await message.channel.send(f"🎲 {roll_player.capitalize()} rolls {dice_expr}: {total}\n{detail}")
            del roll_prompts[channel_id]
            queue = turn_order[channel_id]
            if queue:
                queue.rotate(-1)
                await message.channel.send(f"🔄 **Next Turn:** {queue[0].capitalize()}")
            return
        else:
            await message.channel.send(f"Waiting for **{roll_info['player'].capitalize()}** to roll for {roll_info['reason']}. Use `!roll {roll_info['player']} [dice]`.")
            return

    # Commands handling
    if command == "!stats" and args:
        resolved_name = resolve_player_name(args[0])
        if not resolved_name:
            await message.channel.send("Character not found.")
            return
        stats = format_player_stats(resolved_name)
        await message.channel.send(stats)
        return

    if command == "!hp" and len(args) >= 2:
        resolved_name = resolve_player_name(args[0])
        if not resolved_name:
            await message.channel.send("Character not found.")
            return
        try:
            change = int(args[1])
            data = player_data[resolved_name]
            data["current_hp"] = max(0, min(data["hp"], data["current_hp"] + change))
            save_player_data()
            await message.channel.send(f"{resolved_name.capitalize()}'s HP is now {data['current_hp']}/{data['hp']}.")
        except ValueError:
            await message.channel.send("Invalid HP change amount.")
        return

    if command == "!inv" and args:
        resolved_name = resolve_player_name(args[0])
        if not resolved_name:
            await message.channel.send("Character not found.")
            return
        data = player_data[resolved_name]
        if data.get("inventory"):
            await message.channel.send(f"**{resolved_name.capitalize()}'s Inventory:**\n" + "\n".join(f"- {item}" for item in data["inventory"]))
        else:
            await message.channel.send("No inventory found.")
        return

    if command == "!spells" and args:
        resolved_name = resolve_player_name(args[0])
        if not resolved_name:
            await message.channel.send("Character not found.")
            return
        data = player_data[resolved_name]
        if data.get("spells"):
            await message.channel.send(f"**{resolved_name.capitalize()}'s Spells:**\n" + "\n".join(f"- {s}" for s in data["spells"]))
        else:
            await message.channel.send("No spells found.")
        return

    if command == "!party":
        characters = ", ".join(player_data[name].get("name", name).capitalize() for name in player_data)
        await message.channel.send(f"**Party Members:** {characters}")
        return

    if command == "!turn":
        queue = turn_order[channel_id]
        if queue:
            await message.channel.send(f"**Current Turn:** {queue[0].capitalize()}")
        else:
            await message.channel.send("No turn order set. Use `!resetturn name1 name2 ...`")
        return

    if command == "!next":
        queue = turn_order[channel_id]
        if queue:
            queue.rotate(-1)
            await message.channel.send(f"**Next Turn:** {queue[0].capitalize()}")
        else:
            await message.channel.send("No turn order set.")
        return

    if command == "!skip":
        queue = turn_order[channel_id]
        if not queue:
            await message.channel.send("No turn order set. Use `!resetturn` first.")
            return
        skipped = queue[0].capitalize()
        queue.rotate(-1)
        await message.channel.send(f"⏭️ **{skipped}'s turn skipped.** Next up: **{queue[0].capitalize()}**")
        return

    if command == "!resetturn" and args:
        resolved_names = []
        not_found = []
        for name in args:
            resolved = resolve_player_name(name)
            if resolved:
                resolved_names.append(resolved)
            else:
                not_found.append(name)
        if not_found:
            await message.channel.send(f"Characters not found: {', '.join(not_found)}")
            return
        turn_order[channel_id] = deque(resolved_names)
        await message.channel.send(f"Turn order reset: {', '.join(name.capitalize() for name in resolved_names)}")
        return

    if command == "!dm" and len(args) >= 2:
        print("DM command triggered")
        input_name = args[0].lower()
        action = " ".join(args[1:])

    # Resolve player or party name
        if input_name != "party":
            resolved_name = resolve_player_name(input_name)
            if not resolved_name:
                await message.channel.send("Character not found.")
                return
        m = re.search(
            rf"\b({ '|'.join(flavor_data.keys()) })\b\s+(?:the\s+)?(\w+)",
            action,
            re.IGNORECASE
        )
        print(f"[DEBUG] Action input: {action}")
        print(f"[DEBUG] VERB_PATTERN: {VERB_PATTERN}")
        print(f"[DEBUG] Regex match: {m.groups() if m else 'No match'}")
        if m:
            verb, target = m.group(1).lower(), m.group(2).lower()
            channel_id = message.channel.id

            # ensure monster exists
            monsters = monsters_by_channel[channel_id]
            if target not in monsters:
                monsters[target] = {"hp": 10, "max_hp": 10}
            print(f"[DEBUG] Target: {target}, Monster HP: {monsters.get(target)}")
            monster = monsters[target]
            prof = player_data[resolved_name]["proficiency_bonus"]
            dmg = random.randint(1, 8) + prof
            monster["hp"] = max(0, monster["hp"] - dmg)

            # AI narration
            prompt = (
                f"The current scene is a dark horror-themed D&D campaign. "
                f"{resolved_name.capitalize()} attempts to {verb} the {target}, dealing {dmg} damage. "
                f"The {target} now has {monster['hp']}/{monster['max_hp']} HP. "
                "Continue the story in a gothic, eerie tone using 2-4 vivid sentences. "
                "Avoid repeating phrases. Focus on atmosphere, emotional tension, and natural continuation from the action."
            )
            ai_resp = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role":"system","content":system_prompt},
                          {"role":"user","content":prompt}],
                temperature=0.6,
                max_tokens=100
            )
            narration = ai_resp.choices[0].message.content.strip()
            await message.channel.send(
                f"🎲 **{resolved_name.capitalize()}** deals **{dmg}** damage to the **{target}**.\n"
                f"{narration}\n"
                f"**{target.capitalize()} HP:** {monster['hp']}/{monster['max_hp']}"
    )
            # advance turn
            turn_order[channel_id].rotate(-1)
            await message.channel.send(
                f"🔄 **Next Turn:** {turn_order[channel_id][0].capitalize()}"
            )
            return
        queue = turn_order[channel_id]
        if not queue:
            await message.channel.send("No turn order set. Use `!resetturn` first.")
            return

        current_turn = queue[0].lower()
        if resolved_name.lower() != current_turn:
            await message.channel.send(f"It's not {resolved_name.capitalize()}'s turn! Current turn: **{queue[0].capitalize()}**")
            return

    # If no matched action or fallback, use AI to generate a response
    player_context = get_player_context(action)
    if player_context:
        action += f"\n\n[Player Context]\n{player_context}"

    history = conversation_histories[channel_id]
    history.append({"role": "user", "content": f"{resolved_name.capitalize()} attempts: {action}"})

    # Keep only last 10 messages to limit context size
    if len(history) > 10:
        history = history[-10:]
        conversation_histories[channel_id] = history

    messages = [system_prompt] + list(history)

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            max_tokens=150,
            temperature=0.4,
            top_p=0.7
        )
        reply = response.choices[0].message.content if response.choices else "No response from AI."
        history.append({"role": "assistant", "content": reply})
        await message.channel.send(f"**{resolved_name.capitalize()}**: {reply}")
    except Exception as e:
        await message.channel.send(f"Error: {str(e)}")
        return

    # Advance turn if applicable and not party
    if input_name != "party":
        queue.rotate(-1)
        await message.channel.send(f"🔄 **Next Turn:** {queue[0].capitalize()}")
    return

    if command == "!continue":
        channel_id = message.channel.id
        history = conversation_histories[channel_id]
        if not history:
            await message.channel.send("No previous story context to continue.")
            return

        messages = [system_prompt] + list(history)

        try:
            response = await openai_client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                max_tokens=150,
                temperature=0.6,
                top_p=0.8
            )
            reply = response.choices[0].message.content.strip()
            history.append({"role": "assistant", "content": reply})
            await message.channel.send(reply)
        except Exception as e:
            await message.channel.send(f"Error continuing story: {str(e)}")
        return

    if command == "!addplayer" and len(args) >= 6:
        name = args[0].strip()
        race = args[1]
        char_class = args[2]
        try:
            hp = int(args[3])
            ac = int(args[4])
            dex = int(args[5])
        except ValueError:
            await message.channel.send("HP, AC, and DEX must be numbers.")
            return

        # Autofill
        base_hp, prof_bonus, features = autofill_player_info(race, char_class)
        current_hp = hp if hp > 0 else base_hp
        dex_mod = (dex - 10) // 2

        player_data[name.lower()] = {
            "name": name,
            "race": race,
            "class": char_class,
            "hp": hp,
            "current_hp": current_hp,
            "ac": ac,
            "dex": dex,
            "dex_mod": dex_mod,
            "proficiency_bonus": prof_bonus,
            "features": features,
            "weapons": [],
            "inventory": [],
            "spells": [],
            "aliases": []
        }
        save_player_data()
        await message.channel.send(f"Player **{name}** added with race {race} and class {char_class}.")
        return

    if command == "!alias" and len(args) >= 2:
        player = resolve_player_name(args[0])
        if not player:
            await message.channel.send("Character not found.")
            return
        alias = args[1]
        if alias not in player_data[player].get("aliases", []):
            player_data[player].setdefault("aliases", []).append(alias)
            save_player_data()
            await message.channel.send(f"Alias '{alias}' added for {player.capitalize()}.")
        else:
            await message.channel.send(f"Alias '{alias}' already exists for {player.capitalize()}.")
        return

    if command == "!help":
    	help_text = (
        "**Commands:**\n"
        "`!stats [name]` - Show player stats\n"
        "`!hp [name] [change]` - Adjust HP\n"
        "`!inv [name]` - Show inventory\n"
        "`!spells [name]` - Show spells\n"
        "`!party` - Show party members\n"
        "`!turn` - Show current turn\n"
        "`!next` - Advance turn\n"
        "`!skip` - Skip current turn\n"
        "`!resetturn [names...]` - Set turn order\n"
        "`!dm [name|party] [action]` - DM action\n"
        "`!roll [name] [dice]` - Roll dice\n"
        "`!addplayer [name] [race] [class] [hp] [ac] [dex]` - Add player\n"
        "`!alias [name] [alias]` - Add alias\n"
    )
    await message.channel.send(help_text)
    return


    # Fallback message if unknown command or no command
    # Optionally ignore or respond with help or error
    # await message.channel.send("Unknown command or invalid usage. Use !help for commands.")

discord_client.run(discord_token)
