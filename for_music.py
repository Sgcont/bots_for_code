import discord
from discord.ext import commands, tasks
import yt_dlp
import asyncio
import os
import json
import random
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo # fuso horário (no Windows precisa de: pip install tzdata)
from dotenv import load_dotenv

load_dotenv()  # carrega as variáveis de ambiente do arquivo .env

# 
# CONFIGURAÇÕES (depois mexer aqui pra adaptar ao servidor da nossa equipe for_code)
# 

NOME_CANAL_BOAS_VINDAS = 'boas-vindas'   # canal onde o bot recebe os novatos
NOME_CARGO_INICIAL = 'Calouro'           # cargo que todo mundo ganha ao entrar
NOME_CANAL_DESAFIO = 'desafio-do-dia'    # canal onde o desafio diário é postado
FUSO = ZoneInfo('America/Sao_Paulo')     # horário de Brasília
ARQUIVO_DADOS = 'dados.json'             # onde o bot guarda XP e eventos
XP_POR_NIVEL = 100                       # a cada 100 XP o membro sobe de nível

# configuração das intents (pra ele conseguir ler mensagens, comandos e ver quem entra no servidor)
intents = discord.Intents.all()

# inicializar o bot
bot = commands.Bot(command_prefix='!', intents=intents)

# yt-dlp (baixa)  →  FFmpeg (processa)  →  Discord (toca)

# configurações do yt-dlp e do ffmpeg
YTDL_OPTIONS = {
    'format': 'bestaudio/best', # melhor arquivo de áudio ou melhor arquivo único que tiver áudio
    'noplaylist': True, # se a url apontar pra uma playlist, vai baixar só aquele vídeo e ignorar o resto
    'quiet': True, # silencia a saída, não imprime mensagens de download nem status
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5', # tenta reconectar caso caia a conexão
    'options': '-vn' # diz pra ignorar o vídeo, foca só no áudio
}

# ------------------------------------------------------------
# DADOS (guardados em arquivo json pra não perder quando o bot reiniciar)
# ------------------------------------------------------------
if os.path.exists(ARQUIVO_DADOS): # se o arquivo já existe, carrega o que tinha salvo
    with open(ARQUIVO_DADOS, 'r', encoding='utf-8') as arquivo:
        dados = json.load(arquivo)
else: # senão começa do zero
    dados = {'xp': {}, 'eventos': []}

# dicionários que vivem só na memória (somem quando o bot reinicia, e tudo bem)
filas = {}            # id do servidor -> lista de músicas esperando pra tocar
musica_atual = {}     # id do servidor -> música que está tocando agora
desafios_ativos = {}  # id do servidor -> desafio que está valendo agora
ultimo_xp = {}        # id do usuário -> quando ganhou XP pela última vez (cooldown anti-spam)

# questões do desafio do dia (é só ir adicionando mais aqui!)
# 'respostas' tem que estar em minúsculo, porque o bot compara com a resposta já em minúsculo
DESAFIOS = [
    {
        'titulo': 'Soma dos pares',
        'enunciado': 'O que esse código imprime?\n```python\nprint(sum([x for x in range(10) if x % 2 == 0]))\n```',
        'respostas': ['20'],
        'xp': 20,
    },
    {
        'titulo': 'Tamanho do nome',
        'enunciado': 'O que esse código imprime?\n```python\nprint(len("for_code"))\n```',
        'respostas': ['8'],
        'xp': 15,
    },
    {
        'titulo': 'Potência embaralhada',
        'enunciado': 'O que esse código imprime?\n```python\nprint(2 ** 3 ** 2)\n```\nDica: a potência é calculada da direita pra esquerda!',
        'respostas': ['512'],
        'xp': 25,
    },
    {
        'titulo': 'Texto de trás pra frente',
        'enunciado': 'O que esse código imprime?\n```python\nprint("abc"[::-1])\n```',
        'respostas': ['cba'],
        'xp': 20,
    },
    {
        'titulo': 'Listas com pegadinha',
        'enunciado': 'O que esse código imprime?\n```python\nx = [1, 2, 3]\ny = x\ny.append(4)\nprint(len(x))\n```',
        'respostas': ['4'],
        'xp': 30,
    },
]

# ------------------------------------------------------------
# FUNÇÕES AUXILIARES (só as que são reaproveitadas em vários lugares)
# ------------------------------------------------------------
def salvar_dados():
    with open(ARQUIVO_DADOS, 'w', encoding='utf-8') as arquivo:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2) # ensure_ascii=False mantém os acentos


async def dar_xp(membro, quantidade, canal):
    id_membro = str(membro.id) # json só aceita chaves em texto, por isso o str()
    xp_antes = dados['xp'].get(id_membro, 0) # .get com 0 de padrão: quem nunca ganhou XP começa do zero
    xp_depois = xp_antes + quantidade
    dados['xp'][id_membro] = xp_depois
    salvar_dados()

    # "//" é divisão inteira: se o nível mudou, é porque a pessoa subiu!
    if xp_depois // XP_POR_NIVEL > xp_antes // XP_POR_NIVEL:
        await canal.send(f"🎉 {membro.mention} subiu para o **nível {xp_depois // XP_POR_NIVEL}**!")


def montar_desafio(id_servidor, sortear):
    # sortear=True força um desafio novo, sortear=False reaproveita o que já está valendo
    if sortear or id_servidor not in desafios_ativos:
        desafio = random.choice(DESAFIOS).copy() # .copy() pra não mexer na lista original
        desafio['acertaram'] = [] # ids de quem já acertou esse desafio
        desafios_ativos[id_servidor] = desafio

    desafio = desafios_ativos[id_servidor]
    embed = discord.Embed(
        title=f"🧩 Desafio do dia: {desafio['titulo']}",
        description=desafio['enunciado'],
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Responda com !responder <sua resposta> | vale {desafio['xp']} XP")
    return embed


async def tocar_proxima(ctx):
    voice_client = ctx.voice_client
    if not voice_client: # o bot já saiu do canal, não tem o que tocar
        return

    fila = filas.get(ctx.guild.id, [])
    if len(fila) == 0: # acabou a fila
        musica_atual.pop(ctx.guild.id, None) # pop com None de padrão não dá erro se a chave não existir
        return

    musica = fila.pop(0) # tira a primeira da fila (a mais antiga)
    musica_atual[ctx.guild.id] = musica
    fonte_audio = discord.FFmpegPCMAudio(musica['url'], **FFMPEG_OPTIONS)

    # o "after" roda quando a música acaba, mas em outra thread! por isso o run_coroutine_threadsafe
    # (é o jeito de chamar uma função async de dentro de uma thread comum)
    voice_client.play(
        fonte_audio,
        after=lambda erro: asyncio.run_coroutine_threadsafe(tocar_proxima(ctx), bot.loop)
    )
    await ctx.send(f"🎶 Tocando agora: **{musica['titulo']}**! aproveite!")

# ------------------------------------------------------------
# EVENTOS DO BOT
# ------------------------------------------------------------
@bot.event # bot event é um evento que o bot vai escutar, por ex quando ele está pronto ou quando alguém manda mensagem!
async def on_ready():
    print(f'Bot {bot.user.name} está pronto para ajudar a for_code! :)')
    # o on_ready pode disparar mais de uma vez (se a conexão cair), então confere se a tarefa já não está rodando
    if not checar_eventos.is_running():
        checar_eventos.start()
    if not desafio_diario.is_running():
        desafio_diario.start()


@bot.event
async def on_member_join(membro): # dispara quando alguém entra no servidor
    # dá o cargo inicial (o cargo do bot precisa estar ACIMA do "Calouro" na lista de cargos do servidor)
    cargo = discord.utils.get(membro.guild.roles, name=NOME_CARGO_INICIAL)
    if cargo:
        try:
            await membro.add_roles(cargo)
        except discord.Forbidden:
            print(f"Sem permissão pra dar o cargo {NOME_CARGO_INICIAL}!")

    # manda a mensagem de boas-vindas no canal certo
    canal = discord.utils.get(membro.guild.text_channels, name=NOME_CANAL_BOAS_VINDAS)
    if canal:
        embed = discord.Embed(
            title=f"Bem-vindo(a) à for_code, {membro.display_name}! 🎉",
            description=(
                "Aqui a gente aprende, programa e se diverte junto.\n\n"
                "👉 Dê uma olhada nos canais do servidor\n"
                "👉 Participe do desafio diário com `!desafio`\n"
                "👉 Veja os próximos eventos com `!agenda`"
            ),
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=membro.display_avatar.url) # foto de perfil do novato
        await canal.send(content=membro.mention, embed=embed)


@bot.event
async def on_message(message):
    if message.author.bot or not message.guild: # ignora outros bots e mensagens privadas
        return

    # XP por conversar, com cooldown de 60s pra ninguém ganhar XP spammando
    agora = datetime.now()
    ultima_vez = ultimo_xp.get(message.author.id)
    if ultima_vez is None or agora - ultima_vez > timedelta(seconds=60):
        ultimo_xp[message.author.id] = agora
        await dar_xp(message.author, random.randint(5, 15), message.channel)

    # IMPORTANTE: quando a gente sobrescreve o on_message, os comandos (!tocar etc) param de funcionar
    # a não ser que a gente chame isso aqui no final
    await bot.process_commands(message)


@bot.check # check global: roda antes de TODO comando
async def so_em_servidor(ctx):
    return ctx.guild is not None # comandos só funcionam dentro do servidor, não no privado


@bot.event
async def on_command_error(ctx, erro):
    if isinstance(erro, commands.MissingRequiredArgument): # faltou escrever alguma parte do comando
        await ctx.send(f"Faltou alguma coisa! Use assim: `!{ctx.command.name} {ctx.command.signature}`")
    elif isinstance(erro, commands.MissingPermissions): # tem que vir antes do CheckFailure, porque é um tipo dele
        await ctx.send("Você não tem permissão pra usar esse comando 😅")
    elif isinstance(erro, commands.CheckFailure):
        await ctx.send("Esse comando só funciona dentro do servidor!")
    elif isinstance(erro, commands.CommandNotFound):
        pass # comando que não existe, só ignora
    else:
        print(f"Erro no comando: {erro}")
        await ctx.send("Ops, algo deu errado por aqui 😵")

# 
# 1) MÚSICA COM FILA
# 

@bot.command() # bot command é um comando que o bot vai escutar, por ex quando alguém digita !tocar
async def tocar(ctx, *, busca: str): # o "*" faz a busca pegar o resto da mensagem inteira (nome com espaços)
    # verifica se usuário está em um canal de voz:
    if not ctx.author.voice:
        return await ctx.send("Você precisa estar em um canal de voz para conseguir escutar músicas!")

    # conecta ao canal de voz (ou reaproveita a conexão que já existe)
    if not ctx.voice_client:
        voice_client = await ctx.author.voice.channel.connect()
    else:
        voice_client = ctx.voice_client

    await ctx.send("Procurando sua música....")

    # se não for um link, procura no youtube pelo nome ("ytsearch:" faz o yt-dlp buscar)
    if not busca.startswith('http'):
        busca = f"ytsearch:{busca}"

    # extrair infos da música com o yt-dlp
    try:
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            # o extract_info trava o bot até terminar, então rodo ele em outra thread (run_in_executor)
            info = await bot.loop.run_in_executor(None, lambda: ydl.extract_info(busca, download=False))
    except yt_dlp.utils.DownloadError:
        return await ctx.send("Não consegui carregar essa música 😢")

    if 'entries' in info: # resultado de busca vem como lista de resultados, pega o primeiro
        if len(info['entries']) == 0:
            return await ctx.send("Não achei nada com esse nome 😢")
        info = info['entries'][0]

    # coloca na fila do servidor (setdefault cria uma lista vazia se o servidor ainda não tiver fila)
    fila = filas.setdefault(ctx.guild.id, [])
    fila.append({'url': info['url'], 'titulo': info['title']})

    if voice_client.is_playing() or voice_client.is_paused(): # já tem música rolando, então só entra na fila
        await ctx.send(f"➕ Adicionada à fila (posição {len(fila)}): **{info['title']}**")
    else: # não tem nada tocando, começa agora
        await tocar_proxima(ctx)


@bot.command(name='fila')
async def mostrar_fila(ctx):
    fila = filas.get(ctx.guild.id, [])
    atual = musica_atual.get(ctx.guild.id)

    if not atual and not fila:
        return await ctx.send("A fila está vazia! Use !tocar pra adicionar músicas.")

    texto = ""
    if atual:
        texto += f"🎶 **Tocando agora:** {atual['titulo']}\n\n"
    for i, musica in enumerate(fila[:10], start=1): # start=1 pra numerar a partir do 1, e [:10] limita a 10 músicas
        texto += f"**{i}.** {musica['titulo']}\n"
    if len(fila) > 10:
        texto += f"... e mais {len(fila) - 10} músicas"
    await ctx.send(texto)


@bot.command()
async def tocando(ctx):
    atual = musica_atual.get(ctx.guild.id)
    if atual:
        await ctx.send(f"🎶 Tocando agora: **{atual['titulo']}**")
    else:
        await ctx.send("Não estou tocando nada no momento.")


@bot.command()
async def pular(ctx):
    voice_client = ctx.voice_client
    if voice_client and (voice_client.is_playing() or voice_client.is_paused()):
        voice_client.stop() # parar a música atual dispara o "after" do play, que já chama a próxima da fila sozinho
        await ctx.send("⏭️ Pulando pra próxima!")
    else:
        await ctx.send("Não tem nada tocando pra pular.")


@bot.command()
async def pausa(ctx):
    voice_client = ctx.voice_client
    if voice_client and voice_client.is_playing(): # tem que verificar se tem conexão em algum canal && se está tocando algo
        voice_client.pause()
        await ctx.send("⏸️ Música pausada!")
    else:
        await ctx.send("Não estou tocando nenhuma música no momento.")


@bot.command()
async def volta(ctx):
    voice_client = ctx.voice_client
    if voice_client and voice_client.is_paused(): # verifica se o bot está pausado
        voice_client.resume()
        await ctx.send("▶️ Voltando com sua música!")
    else:
        await ctx.send("Não tem nenhuma música pausada.")


@bot.command()
async def para(ctx):
    voice_client = ctx.voice_client
    if voice_client:
        filas[ctx.guild.id] = [] # esvazia a fila ANTES de parar, senão o after tocaria a próxima música
        voice_client.stop()
        await voice_client.disconnect()
        await ctx.send("⏹️ Música parada e vou desconectar! Tchauuuu 👋")

# 
# 2) DESAFIO DE PROGRAMAÇÃO DO DIA
#

@tasks.loop(time=time(hour=9, minute=0, tzinfo=FUSO)) # roda todo dia às 9h de Brasília
async def desafio_diario():
    for servidor in bot.guilds: # passa por todos os servidores onde o bot está
        canal = discord.utils.get(servidor.text_channels, name=NOME_CANAL_DESAFIO)
        if canal:
            await canal.send(embed=montar_desafio(servidor.id, True)) # True = sorteia um desafio novo


@bot.command(name='desafio')
async def pedir_desafio(ctx):
    await ctx.send(embed=montar_desafio(ctx.guild.id, False)) # False = mostra o que já está valendo


@bot.command()
async def responder(ctx, *, resposta: str):
    try:
        await ctx.message.delete() # apaga a mensagem da pessoa pra ninguém copiar a resposta
    except discord.Forbidden:
        pass # sem permissão de apagar mensagens, segue o jogo

    desafio = desafios_ativos.get(ctx.guild.id)
    if not desafio:
        return await ctx.send("Não tem nenhum desafio ativo! Use !desafio pra ver o de hoje.")

    if ctx.author.id in desafio['acertaram']: # evita ganhar XP duas vezes no mesmo desafio
        return await ctx.send(f"{ctx.author.mention}, você já acertou esse! 😉")

    if resposta.strip().lower() in desafio['respostas']: # strip tira espaços das pontas, lower deixa tudo minúsculo
        desafio['acertaram'].append(ctx.author.id)
        await ctx.send(f"✅ {ctx.author.mention} acertou! +{desafio['xp']} XP")
        await dar_xp(ctx.author, desafio['xp'], ctx.channel)
    else:
        await ctx.send(f"❌ {ctx.author.mention}, não foi dessa vez! Tenta de novo.")

# 
# 3) AGENDA DE EVENTOS DA for_code
# 

@bot.command()
@commands.has_permissions(manage_guild=True) # só quem tem a permissão "Gerenciar Servidor" (diretoria) cria evento
async def evento(ctx, data: str, hora: str, *, titulo: str):
    # uso: !evento 25/10/2026 19:00 Minicurso de Python
    try:
        # strptime transforma o texto em data; .replace(tzinfo=...) diz que é horário de Brasília
        quando = datetime.strptime(f"{data} {hora}", "%d/%m/%Y %H:%M").replace(tzinfo=FUSO)
    except ValueError:
        return await ctx.send("Formato inválido! Use: `!evento 25/10/2026 19:00 Minicurso de Python`")

    if quando <= datetime.now(FUSO):
        return await ctx.send("Essa data já passou 😅")

    dados['eventos'].append({
        'titulo': titulo,
        'quando': quando.isoformat(), # json não guarda datas, então salvo como texto e converto de volta quando precisar
        'canal_id': ctx.channel.id, # o aviso vai ser mandado no mesmo canal onde o evento foi criado
        'guild_id': ctx.guild.id,
        'avisado': False,
    })
    dados['eventos'].sort(key=lambda e: datetime.fromisoformat(e['quando'])) # deixa em ordem do mais próximo pro mais distante
    salvar_dados()
    await ctx.send(f"📅 Evento **{titulo}** marcado para {data} às {hora}! Vou avisar 30 minutos antes.")


@bot.command()
async def agenda(ctx):
    do_servidor = [e for e in dados['eventos'] if e['guild_id'] == ctx.guild.id] # só os eventos deste servidor
    if not do_servidor:
        return await ctx.send("Nenhum evento marcado por enquanto 📭")

    linhas = []
    for i, e in enumerate(do_servidor, start=1):
        quando = datetime.fromisoformat(e['quando'])
        linhas.append(f"**{i}.** {e['titulo']} - {quando.strftime('%d/%m/%Y às %H:%M')}")

    embed = discord.Embed(title="📅 Próximos eventos da for_code", description="\n".join(linhas), color=discord.Color.purple())
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(manage_guild=True)
async def cancelarevento(ctx, numero: int): # o número é o da lista do !agenda
    do_servidor = [e for e in dados['eventos'] if e['guild_id'] == ctx.guild.id]
    if numero < 1 or numero > len(do_servidor):
        return await ctx.send("Número inválido! Veja a lista com !agenda")

    removido = do_servidor[numero - 1] # -1 porque a lista começa no 0 mas a gente mostra a partir do 1
    dados['eventos'].remove(removido) # tira esse mesmo dicionário da lista geral
    salvar_dados()
    await ctx.send(f"🗑️ Evento **{removido['titulo']}** cancelado!")


@tasks.loop(minutes=1) # confere a agenda de minuto em minuto
async def checar_eventos():
    agora = datetime.now(FUSO)
    mudou = False

    for e in dados['eventos']:
        falta = datetime.fromisoformat(e['quando']) - agora # subtrair duas datas dá um timedelta
        if not e['avisado'] and timedelta(0) < falta <= timedelta(minutes=30):
            canal = bot.get_channel(e['canal_id'])
            if canal:
                minutos = round(falta.total_seconds() / 60)
                await canal.send(f"⏰ Atenção! **{e['titulo']}** começa em {minutos} minutos!")
            e['avisado'] = True
            mudou = True

    # joga fora os eventos que já começaram
    restantes = [e for e in dados['eventos'] if datetime.fromisoformat(e['quando']) > agora]
    if mudou or len(restantes) != len(dados['eventos']): # só salva no arquivo se algo mudou
        dados['eventos'] = restantes
        salvar_dados()

# 
# 4) XP E RANKING
# 

@bot.command()
async def rank(ctx, membro: discord.Member = None): # o discord.Member converte a menção (@fulano) no membro sozinho
    membro = membro or ctx.author # se não marcou ninguém, usa quem digitou o comando
    xp = dados['xp'].get(str(membro.id), 0)

    # lambda par: par[1] -> cada "par" é (id, xp), então ordena pelo XP; reverse=True deixa do maior pro menor
    ids_ordenados = [uid for uid, valor in sorted(dados['xp'].items(), key=lambda par: par[1], reverse=True)]
    if str(membro.id) in ids_ordenados:
        posicao = ids_ordenados.index(str(membro.id)) + 1
    else:
        posicao = '-'

    embed = discord.Embed(title=f"📊 Rank de {membro.display_name}", color=discord.Color.orange())
    embed.add_field(name="Nível", value=xp // XP_POR_NIVEL)
    embed.add_field(name="XP total", value=xp)
    embed.add_field(name="Posição", value=f"{posicao}º")
    embed.set_footer(text=f"Faltam {XP_POR_NIVEL - xp % XP_POR_NIVEL} XP pro próximo nível") # % é o resto da divisão
    embed.set_thumbnail(url=membro.display_avatar.url)
    await ctx.send(embed=embed)


@bot.command()
async def ranking(ctx):
    top = sorted(dados['xp'].items(), key=lambda par: par[1], reverse=True)[:10] # só os 10 primeiros
    if not top:
        return await ctx.send("Ninguém tem XP ainda! Bora conversar e resolver desafios 💪")

    medalhas = ['🥇', '🥈', '🥉']
    linhas = []
    for i, (uid, xp) in enumerate(top): # enumerate dá o índice junto com o item, e (uid, xp) desempacota a tupla
        marca = medalhas[i] if i < 3 else f"**{i + 1}.**"
        linhas.append(f"{marca} <@{uid}> - {xp} XP (nível {xp // XP_POR_NIVEL})") # <@id> vira a menção do usuário

    embed = discord.Embed(title="🏆 Ranking da for_code", description="\n".join(linhas), color=discord.Color.gold())
    await ctx.send(embed=embed)


bot.run(os.getenv('TOKEN_DO_BOT'))
