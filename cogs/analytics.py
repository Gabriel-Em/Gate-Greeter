import discord
from discord.ext import commands
import os
import json
import matplotlib.pyplot as plt
import io
from datetime import datetime, timedelta

class Analytics(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="archive")
    async def archive(self, ctx):
        if os.getenv("ENABLE_CMD_ARCHIVE", "True").lower() not in ("true", "1", "t"):
            return # Command is disabled

        status_msg = await ctx.send("🗄️ Initializing 12-month deep scan. This will take a while...")
        os.makedirs('monthly_stats', exist_ok=True)
        now = datetime.now() 
        
        for i in range(12):
            y = now.year + (now.month - 1 - i) // 12
            m = (now.month - 1 - i) % 12 + 1
            
            start_dt = datetime(y, m, 1)
            end_dt = (datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)) - timedelta(seconds=1)
                
            is_complete = end_dt < now
            filename = f"monthly_stats/stats_{y}_{m:02d}.json"
            
            if os.path.exists(filename):
                try:
                    with open(filename, 'r') as f:
                        if json.load(f).get('complete', False):
                            continue
                except json.JSONDecodeError:
                    pass
                        
            await status_msg.edit(content=f"🗄️ Scraping data for {y}-{m:02d} (Month {i+1} of 12)...")
            month_data = {"complete": is_complete, "users": {}}
            total_channels = len(ctx.guild.text_channels)
            
            for idx, channel in enumerate(ctx.guild.text_channels, 1):
                if not channel.permissions_for(ctx.guild.me).read_message_history:
                    continue
                if channel.name.lower().endswith("logs"):
                    continue
                    
                if idx % 5 == 0:
                    await status_msg.edit(content=f"🗄️ Scraping data for {y}-{m:02d} (Month {i+1} of 12) — Scanning channel {idx}/{total_channels}...")
                    
                try:
                    async for msg in channel.history(limit=None, after=start_dt, before=end_dt):
                        if msg.author.bot: continue
                            
                        uid = str(msg.author.id)
                        cname = channel.name
                        
                        if uid not in month_data["users"]:
                            month_data["users"][uid] = {"name": str(msg.author), "channels": {}}
                        if cname not in month_data["users"][uid]["channels"]:
                            month_data["users"][uid]["channels"][cname] = 0
                            
                        month_data["users"][uid]["channels"][cname] += 1
                except discord.Forbidden:
                    pass
                    
            with open(filename, 'w') as f:
                json.dump(month_data, f, indent=4)
                
        await status_msg.edit(content="✅ 12-month data archive is up to date!")

    @commands.command(name="activity30")
    async def activity30(self, ctx, target_user_id: str = None):
        if os.getenv("ENABLE_CMD_ACTIVITY30", "True").lower() not in ("true", "1", "t"):
            return # Command is disabled

        if target_user_id:
            target_user_id = target_user_id.replace('<@', '').replace('!', '').replace('>', '')
            status_msg = await ctx.send(f"📈 Compiling metrics for user `{target_user_id}`...")
            title_context = f"User: {target_user_id}"
        else:
            status_msg = await ctx.send("📈 Compiling 30-day activity metrics for the server...")
            title_context = "Server-wide"
        
        now = datetime.now()
        start_dt = now - timedelta(days=30)
        hourly_counts = {i: 0 for i in range(24)}
        daily_counts = {(start_dt + timedelta(days=i)).strftime('%Y-%m-%d'): 0 for i in range(31)}

        for channel in ctx.guild.text_channels:
            if not channel.permissions_for(ctx.guild.me).read_message_history: continue
            if channel.name.lower().endswith("logs"): continue
                
            try:
                async for msg in channel.history(limit=None, after=start_dt):
                    if msg.author.bot: continue
                    if target_user_id and str(msg.author.id) != target_user_id: continue
                    
                    local_time = msg.created_at + timedelta(hours=3)
                    hourly_counts[local_time.hour] += 1
                    
                    day_str = msg.created_at.strftime('%Y-%m-%d')
                    if day_str in daily_counts:
                        daily_counts[day_str] += 1
            except discord.Forbidden:
                pass

        plt.figure(figsize=(10, 5))
        plt.bar(list(hourly_counts.keys()), list(hourly_counts.values()), color='#3b82f6')
        plt.title(f"Activity by Hour (Local Time) [{title_context}]")
        plt.xlabel("Hour (0:00 - 23:00)")
        plt.ylabel("Total Messages")
        plt.xticks(range(24))
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        plt.tight_layout()
        
        buf_hourly = io.BytesIO()
        plt.savefig(buf_hourly, format="png")
        buf_hourly.seek(0)
        plt.close()

        plt.figure(figsize=(12, 5))
        sorted_days = sorted(daily_counts.items())
        plt.plot([i[0] for i in sorted_days], [i[1] for i in sorted_days], marker='o', color='#10b981', linewidth=2)
        plt.title(f"Daily Activity (Last 30 Days) [{title_context}]")
        plt.xlabel("Date")
        plt.ylabel("Messages")
        plt.xticks(rotation=45, ha='right')
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.tight_layout()
        
        buf_daily = io.BytesIO()
        plt.savefig(buf_daily, format="png")
        buf_daily.seek(0)
        plt.close()

        file_hourly = discord.File(fp=buf_hourly, filename="hourly_activity.png")
        file_daily = discord.File(fp=buf_daily, filename="daily_activity.png")
        
        await status_msg.delete()
        await ctx.send(f"Here is the breakdown for the last 30 days ({title_context}):", files=[file_hourly, file_daily])

    @commands.command(name="topusers")
    async def topusers(self, ctx, year: int = None, month: int = None):
        if os.getenv("ENABLE_CMD_TOPUSERS", "True").lower() not in ("true", "1", "t"):
            return # Command is disabled

        if year is None:
            await ctx.send("⚠️ Please provide at least a year. Example: `!topusers 2026` or `!topusers 2026 9`")
            return

        now = datetime.now()
        
        if month is None:
            target_months = list(range(1, 13))
            title = f"{year} Yearly Total"
        else:
            if not (1 <= month <= 12): return await ctx.send("⚠️ Month must be between 1 and 12.")
            target_months = [month]
            title = f"{year}-{month:02d}"

        os.makedirs('monthly_stats', exist_ok=True)
        aggregated_users = {}
        all_months_complete = True
        status_msg = None
        total_channels = len(ctx.guild.text_channels)

        for m in target_months:
            start_dt = datetime(year, m, 1)
            if start_dt > now: continue

            end_dt = (datetime(year + 1, 1, 1) if m == 12 else datetime(year, m + 1, 1)) - timedelta(seconds=1)
            is_complete = end_dt < now
            filename = f"monthly_stats/stats_{year}_{m:02d}.json"
            
            needs_scrape = False
            if not os.path.exists(filename):
                needs_scrape = True
            else:
                try:
                    with open(filename, 'r') as f:
                        if not json.load(f).get('complete', False): needs_scrape = True
                except json.JSONDecodeError:
                    needs_scrape = True

            if needs_scrape:
                all_months_complete = False
                if not status_msg: status_msg = await ctx.send(f"⏳ Gathering missing data for **{title}**...")
                else: await status_msg.edit(content=f"⏳ Gathering missing data for **{title}** (Scanning {year}-{m:02d})...")

                month_data = {"complete": is_complete, "users": {}}

                for idx, channel in enumerate(ctx.guild.text_channels, 1):
                    if not channel.permissions_for(ctx.guild.me).read_message_history: continue
                    if channel.name.lower().endswith("logs"): continue
                        
                    if idx % 5 == 0: await status_msg.edit(content=f"⏳ Gathering data for **{title}** (Scanning {year}-{m:02d}, channel {idx}/{total_channels})...")
                        
                    try:
                        async for msg in channel.history(limit=None, after=start_dt, before=end_dt):
                            if msg.author.bot: continue
                            uid, uname, cname = str(msg.author.id), str(msg.author), channel.name
                            
                            if uid not in month_data["users"]: month_data["users"][uid] = {"name": uname, "channels": {}}
                            if cname not in month_data["users"][uid]["channels"]: month_data["users"][uid]["channels"][cname] = 0
                            month_data["users"][uid]["channels"][cname] += 1
                    except discord.Forbidden: pass

                with open(filename, 'w') as f: json.dump(month_data, f, indent=4)
                existing_data = month_data

            users_dict = existing_data.get("users", {})
            for uid, uinfo in users_dict.items():
                if uid not in aggregated_users: aggregated_users[uid] = {"name": uinfo.get("name", "Unknown User"), "total": 0}
                aggregated_users[uid]["total"] += sum(uinfo.get("channels", {}).values())

        if status_msg: await status_msg.delete()

        if not aggregated_users: return await ctx.send(f"ℹ️ No messages recorded for {title}.")

        user_totals = [(info["name"], info["total"]) for info in aggregated_users.values()]
        user_totals.sort(key=lambda x: x[1], reverse=True)
        top_10 = user_totals[:10]

        embed = discord.Embed(title=f"🏆 Top Active Users ({title})", color=discord.Color.gold())
        embed.description = "\n".join([f"**{rank}.** {name} — {count:,} messages" for rank, (name, count) in enumerate(top_10, 1)])
        embed.set_footer(text="Status: Complete (Loaded from Cache)" if all_months_complete else "Status: Data Updated to Present")
        
        await ctx.send(embed=embed)

    @commands.command(name="pronounstats")
    async def pronounstats(self, ctx, year: int = None):
        if os.getenv("ENABLE_CMD_PRONOUNSTATS", "True").lower() not in ("true", "1", "t"):
            return
            
        if year is None:
            year = datetime.now().year

        status_msg = await ctx.send(f"📊 Calculating active users and retrieving roles for {year}...")

        # Exact roles mapped from the provided image
        target_roles = [
            "He/Him", 
            "She/Her", 
            "They/Them", 
            "Use my name", 
            "Any/All", 
            "Ask for pronouns"
        ]
        
        role_counts = {role: 0 for role in target_roles}
        role_counts["Unknown"] = 0
        
        # We use a set so if a user is active in multiple months, they are only counted once
        active_users = set()

        # 1. Identify all active users for the specified year using the archives
        for month in range(1, 13):
            filename = f"monthly_stats/stats_{year}_{month:02d}.json"
            if os.path.exists(filename):
                try:
                    with open(filename, 'r') as f:
                        data = json.load(f)
                        for uid, info in data.get("users", {}).items():
                            total_msgs = sum(info.get("channels", {}).values())
                            if total_msgs >= 50:
                                active_users.add(int(uid))
                except json.JSONDecodeError:
                    pass

        if not active_users:
            await status_msg.delete()
            return await ctx.send(f"ℹ️ No active users (50+ messages in a month) found in the archives for {year}.")

        # 2. Check current server roles for each active user
        for uid in active_users:
            member = ctx.guild.get_member(uid)
            if member:
                member_role_names = [r.name for r in member.roles]
                matched_any = False
                
                # Check for each specific role
                for tr in target_roles:
                    if tr in member_role_names:
                        role_counts[tr] += 1
                        matched_any = True
                        
                # If they have none of the target roles, mark as Unknown
                if not matched_any:
                    role_counts["Unknown"] += 1
            else:
                # If the user left the server, we cannot check their roles
                role_counts["Unknown"] += 1

        # 3. Generate the Bar Chart
        plt.figure(figsize=(12, 6))
        categories = list(role_counts.keys())
        counts = list(role_counts.values())
        
        # Color palette: Orange for the target roles (to match the icon), Grey for Unknown
        colors = ['#f97316'] * len(target_roles) + ['#64748b']
        
        bars = plt.bar(categories, counts, color=colors)
        plt.title(f"Active User Demographics for {year}\n(Active = 50+ messages in a single month)")
        plt.xlabel("Assigned Roles")
        plt.ylabel("Number of Active Users")
        plt.xticks(rotation=45, ha='right')
        plt.grid(axis='y', linestyle='--', alpha=0.7)
        
        # Add the numerical value on top of each bar for easier reading
        for bar in bars:
            yval = bar.get_height()
            plt.text(bar.get_x() + bar.get_width()/2, yval + (max(counts) * 0.02), 
                     int(yval), ha='center', va='bottom', fontweight='bold')
                     
        plt.tight_layout()
        
        buf = io.BytesIO()
        plt.savefig(buf, format="png")
        buf.seek(0)
        plt.close()
        
        file = discord.File(fp=buf, filename="pronoun_stats.png")
        await status_msg.delete()
        await ctx.send(f"Here are the active user demographic statistics for {year}:", file=file)

async def setup(bot):
    await bot.add_cog(Analytics(bot))