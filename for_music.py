        desafio['acertaram'].append(ctx.author.id)
        await ctx.send(f"✅ {ctx.author.mention} acertou! +{desafio['xp']} XP")
        await dar_xp(ctx.author, desafio['xp'], ctx.channel)
    else:
        await ctx.send(f"❌ {ctx.author.mention}, não foi dessa vez! Tenta de novo.")

# Registra um evento futuro; só membros com permissão de gerenciar o servidor podem criá-lo.
@bot.command()
@commands.has_permissions(manage_guild=True)
async def evento(ctx, data: str, hora: str, *, titulo: str):

    try:
        # Converte a data e a hora digitadas para uma data no fuso de Brasília.
        quando = datetime.strptime(f"{data} {hora}", "%d/%m/%Y %H:%M").replace(tzinfo=FUSO)
    except ValueError:
        return await ctx.send("Formato inválido! Use: `!evento 25/10/2026 19:00 Minicurso de Python`")

    if quando <= datetime.now(FUSO):
        return await ctx.send("Essa data já passou 😅")

    dados['eventos'].append({
        'titulo': titulo,
        'quando': quando.isoformat(),
        'canal_id': ctx.channel.id,
        'guild_id': ctx.guild.id,
        'avisado': False,
    })
    dados['eventos'].sort(key=lambda e: datetime.fromisoformat(e['quando']))
    salvar_dados()
    await ctx.send(f"📅 Evento **{titulo}** marcado para {data} às {hora}! Vou avisar 30 minutos antes.")

# Lista os eventos cadastrados para este servidor.
@bot.command()
async def agenda(ctx):
    do_servidor = [e for e in dados['eventos'] if e['guild_id'] == ctx.guild.id]
    if not do_servidor:
        return await ctx.send("Nenhum evento marcado por enquanto 📭")

    linhas = []
    for i, e in enumerate(do_servidor, start=1):
        quando = datetime.fromisoformat(e['quando'])
        linhas.append(f"**{i}.** {e['titulo']} - {quando.strftime('%d/%m/%Y às %H:%M')}")

    embed = discord.Embed(title="📅 Próximos eventos da for_code", description="\n".join(linhas), color=discord.Color.purple())
    await ctx.send(embed=embed)

# O número informado corresponde à posição do evento exibido em !agenda.
@bot.command()
@commands.has_permissions(manage_guild=True)
async def cancelarevento(ctx, numero: int):
    do_servidor = [e for e in dados['eventos'] if e['guild_id'] == ctx.guild.id]
    if numero < 1 or numero > len(do_servidor):
        return await ctx.send("Número inválido! Veja a lista com !agenda")

    removido = do_servidor[numero - 1]
    dados['eventos'].remove(removido)
    salvar_dados()
    await ctx.send(f"🗑️ Evento **{removido['titulo']}** cancelado!")

# Verifica a agenda a cada minuto e avisa quando faltam até 30 minutos para um evento.
@tasks.loop(minutes=1)
async def checar_eventos():
    agora = datetime.now(FUSO)
    mudou = False

    for e in dados['eventos']:
        falta = datetime.fromisoformat(e['quando']) - agora
        if not e['avisado'] and timedelta(0) < falta <= timedelta(minutes=30):
            canal = bot.get_channel(e['canal_id'])
            if canal:
                minutos = round(falta.total_seconds() / 60)
                await canal.send(f"⏰ Atenção! **{e['titulo']}** começa em {minutos} minutos!")
            e['avisado'] = True
            mudou = True

    restantes = [e for e in dados['eventos'] if datetime.fromisoformat(e['quando']) > agora]
    if mudou or len(restantes) != len(dados['eventos']):
        dados['eventos'] = restantes
        salvar_dados()

# Exibe o nível, o XP e a posição de um membro no ranking.
@bot.command()
async def rank(ctx, membro: discord.Member = None):
    membro = membro or ctx.author
    xp = dados['xp'].get(str(membro.id), 0)

    ids_ordenados = [uid for uid, valor in sorted(dados['xp'].items(), key=lambda par: par[1], reverse=True)]
    if str(membro.id) in ids_ordenados:
        posicao = ids_ordenados.index(str(membro.id)) + 1
    else:
        posicao = '-'

    embed = discord.Embed(title=f"📊 Rank de {membro.display_name}", color=discord.Color.orange())
    embed.add_field(name="Nível", value=xp // XP_POR_NIVEL)
    embed.add_field(name="XP total", value=xp)
    embed.add_field(name="Posição", value=f"{posicao}º")
    embed.set_footer(text=f"Faltam {XP_POR_NIVEL - xp % XP_POR_NIVEL} XP pro próximo nível")
    embed.set_thumbnail(url=membro.display_avatar.url)
    await ctx.send(embed=embed)

# Mostra os dez membros com mais XP.
@bot.command()
async def ranking(ctx):
    top = sorted(dados['xp'].items(), key=lambda par: par[1], reverse=True)[:10]
    if not top:
        return await ctx.send("Ninguém tem XP ainda! Bora conversar e resolver desafios 💪")

    medalhas = ['🥇', '🥈', '🥉']
    linhas = []
    for i, (uid, xp) in enumerate(top):
        marca = medalhas[i] if i < 3 else f"**{i + 1}.**"
        linhas.append(f"{marca} <@{uid}> - {xp} XP (nível {xp // XP_POR_NIVEL})")

    embed = discord.Embed(title="🏆 Ranking da for_code", description="\n".join(linhas), color=discord.Color.gold())
    await ctx.send(embed=embed)

bot.run(os.getenv('TOKEN_DO_BOT'))
