"""Records a podcast episode: speech, cameras and pacing, orchestrated.

This is an HTTP client, not an editor script -- it runs OUTSIDE the game.
With the game open:

    py podcast.py
    py podcast.py --ia      # answers are improvised by each agent's AI
    py podcast.py --listar  # print the episode script without recording

What it uses, and why:

  /api/camera?angulo=N   the framings saved in camera_angulos.json
  /api/speak             one agent's line, with that agent's voice and persona
  /api/ask               same, but the answer comes from the AI
  /api/status            who is speaking RIGHT NOW -- so cuts land on cue

The rule that makes it feel like a real recording: the camera moves BEFORE
a line starts, never during one. A camera operator frames the shot and only
then the guest speaks; cutting mid-sentence is what gives amateur video away.

Gaze is deliberately mixed. The speaker does not always look at the other
person: introducing yourself is addressed to the CAMERA (to the audience),
while answering a question is addressed to WHOEVER ASKED. The listener looks
at the speaker -- which is what makes the reaction shot feel alive.
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.dirname(AQUI)


def _arquivo_de_config(nome):
    """Find a config file next to the script or in the parent project.

    Inside the Unreal project this script lives in Tools/, so the config
    sits at ../Config/<name>. Published standalone, a Config/ folder next
    to the script works too. Returns the first path that exists, or the
    last candidate (callers handle the failure).
    """
    candidatos = [
        os.path.join(AQUI, "Config", nome),
        os.path.join(RAIZ, "Config", nome),
    ]
    for caminho in candidatos:
        if os.path.exists(caminho):
            return caminho
    return candidatos[-1]


def porta_do_jogo():
    """The API port comes from the [ZCore] section of DefaultGame.ini.

    The same source the game itself reads -- a .ini inside Config/ ships
    with a packaged build, so every tool and the game agree on one port.
    Falls back to 8420 when the file is absent.
    """
    caminho = _arquivo_de_config("DefaultGame.ini")
    try:
        dentro = False
        with open(caminho, "r", encoding="utf-8") as f:
            for linha in f:
                linha = linha.strip()
                if linha.startswith("[") and linha.endswith("]"):
                    dentro = (linha == "[ZCore]")
                elif dentro and linha.startswith("PORTA_JOGO="):
                    valor = linha.split("=", 1)[1].strip().strip('"')
                    if valor.isdigit():
                        return int(valor)
    except Exception:
        pass
    return 8420


BASE = "http://localhost:{}".format(porta_do_jogo())

# How early the next line starts BEFORE the current one ends, in seconds.
#
# Real people do not wait for the full stop to reply -- they come in right
# on top of the sentence ending. Without this, every turn change has a gap
# that betrays machine turn-taking.
#
# Half a second is little: it overlaps the tail of the last word, not the
# sentence.
ANTECIPACAO = 0.5


# --------------------------------------------------------------- the API

def chama(rota, corpo=None, timeout=30):
    """GET, or POST when a body is given. Returns the JSON, or None."""
    url = BASE + rota
    dados = None
    cabecalho = {}
    if corpo is not None:
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        cabecalho["Content-Type"] = "application/json; charset=utf-8"
    try:
        req = urllib.request.Request(url, data=dados, headers=cabecalho)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            texto = r.read().decode("utf-8", "replace")
        return json.loads(texto)
    except urllib.error.URLError as e:
        print("  ! {} failed: {}".format(rota, e))
        return None
    except ValueError:
        return None


def camera(angulo, segundos=None):
    """Travel the camera to a saved angle. Does not wait for arrival."""
    rota = "/api/camera?angulo={}".format(urllib.parse.quote(str(angulo)))
    if segundos:
        rota += "&seg={}".format(segundos)
    return chama(rota)


def espera_camera(limite=6.0):
    """Block until the camera settles.

    The ?angulo= response returns IMMEDIATELY while the camera arrives
    seconds later -- hence its "viajando" (traveling) field. Without this
    wait, a line would start while the frame is still moving.
    """
    fim = time.time() + limite
    while time.time() < fim:
        r = chama("/api/camera")
        if not r or not r.get("viajando"):
            return
        time.sleep(0.15)


_POSES = None


def corta(angulo):
    """HARD CUT to a saved angle -- instantaneous, like broadcast TV.

    The travel (?angulo=) suits the opening shot, but between lines it
    forces a choice between two flaws: a visible camera move while people
    talk, or a silent gap between lines while the camera repositions. The
    cut removes both: zero seconds, zero motion. It is what an interview
    show does on every turn change.

    The jump uses ?por= with the pose read from Config/camera_angulos.json
    -- the same file the game reads for travels, so both paths arrive at
    the SAME framing.
    """
    global _POSES
    if _POSES is None:
        caminho = _arquivo_de_config("camera_angulos.json")
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        _POSES = {a["nome"]: a["por"] for a in dados.get("angulos", [])}
    pose = _POSES.get(str(angulo))
    if not pose:
        print("  ! angle {} has no saved pose -- traveling".format(angulo))
        return camera(angulo)

    # The pose goes RAW in the URL, without urllib.parse.quote: it only
    # contains digits, commas, dots and hyphens -- nothing to encode.
    r = chama("/api/camera?por={}".format(pose))
    if not r or not r.get("ok"):
        # Checking the response is what separates "failed" from "failed
        # silently" -- a silent cut failure leaves the whole episode stuck
        # in one framing.
        print("  ! cut to angle {} FAILED: {}".format(
            angulo, (r or {}).get("erro", "no response")))
    return r


def olhar(quem, alvo=None, parar=False):
    """Intentionally a no-op -- gaze is not commanded anymore.

    Each character decides its own gaze inside the game: it randomly
    alternates between looking at the other person, at the camera, and
    glancing away, at irregular intervals -- and looks LESS at the
    interlocutor while speaking, the way people actually do. Scripted
    gaze looked robotic by comparison.

    The function stays so the EPISODE SCRIPT remains readable: each block
    still declares the gaze INTENT ("mason->zoe"), which is useful
    information for whoever reads the script, even though the game
    resolves it alone.
    """
    return None


def falando(agente):
    """Is this agent speaking RIGHT NOW?"""
    r = chama("/api/status")
    if not r:
        return False
    return bool(r.get("agentes", {}).get(agente, False))


def prepara(agente, texto, usar_ia=False, limite=90.0):
    """GENERATE the audio without playing it. Returns (file, seconds).

    The FILE is the key to play this exact line later, even if other lines
    are prepared in between -- which is what allows pre-generating the
    whole episode. The DURATION is what allows placing a reaction cut at
    an exact point and overlapping one line onto another.
    """
    corpo = {"agente": agente}
    corpo["pergunta" if usar_ia else "texto"] = texto
    chama("/api/prepare", corpo)

    inicio = time.time()
    while time.time() - inicio < limite:
        r = chama("/api/prepared?agente={}".format(agente))
        if r and r.get("pronta"):
            return r.get("arquivo") or "", float(r.get("segundos") or 0.0)
        time.sleep(0.2)

    print("  ! {} was not ready within {:.0f}s".format(agente, limite))
    return "", 0.0


def toca(agente, arquivo=None):
    """Play a prepared line. Returns its duration."""
    corpo = {"agente": agente}
    if arquivo:
        corpo["arquivo"] = arquivo
    r = chama("/api/play", corpo)
    if not r or not r.get("ok"):
        print("  ! /api/play failed for {}: {}".format(
            agente, (r or {}).get("erro", "no response")))
        return 0.0
    return float(r.get("segundos") or 0.0)


def espera_fala(agente, segundos, folga=0.4):
    """Wait for a line to finish.

    Sleeps the known duration instead of polling -- the audio does not
    change size midway. The slack covers the latency between the play call
    and the audio actually starting, and doubles as breathing room.
    """
    time.sleep(segundos + folga)
    # safety net: if the agent is somehow still speaking (audio longer
    # than its header claimed), wait for real.
    fim = time.time() + 10.0
    while time.time() < fim and falando(agente):
        time.sleep(0.2)


# --------------------------------------------------------- the episode
#
# Each block says: who speaks, what they say, from which angle, and where
# each person looks. The angles are the ones saved in
# Config/camera_angulos.json:
#
#   1  wide shot, both in the studio
#   2  guest close-up (Mason)
#   3  host close-up (Zoe)
#
# In-game, the 1/2/3 keys fly the camera to these same angles (they issue
# the same ?angulo= call this script uses).
#
# The grammar behind the choices:
#
#   - Open and close on the WIDE shot (3): it establishes the space at the
#     start and hands it back at the end.
#   - Whoever INTRODUCES themselves speaks to the camera, not to the other
#     person: it is a line addressed to the audience.
#   - Whoever ASKS looks at whoever will answer; whoever ANSWERS looks at
#     whoever asked. That is what ties both into the same conversation.
#   - One REACTION CUT in the middle of the long answer: cut to the
#     listener. The oldest device in edited interviews -- the voice
#     continues while the image changes.
#
# The dialogue is written in Brazilian Portuguese, accents included,
# because the sample voices and personas speak pt-BR -- and TTS engines
# mispronounce unaccented words. Replace the "texto" fields (and the
# voices) to run the show in any language.

ROTEIRO = [
    {
        "nota": "opening -- the wide shot establishes the studio",
        "camera": 1,
        "seg": 2.0,
        "olhar": [("zoe", None), ("mason", None)],
        "quem": "zoe",
        "texto": ("Boa noite, e bem-vindos a mais um episódio do podcast da "
                  "ZCore Network."),
    },
    {
        "nota": "she introduces herself -- close-up, addressing the audience",
        "camera": 3,
        "seg": 2.2,
        "olhar": [("zoe", None)],
        "quem": "zoe",
        "texto": ("Eu sou a Zoe, apresentadora aqui do programa, e todo "
                  "episódio eu converso com alguém que trabalha com "
                  "tecnologia."),
    },
    {
        "nota": "she announces the guest -- turning to him before naming him",
        "camera": 3,
        "seg": 1.8,
        "olhar": [("zoe", "mason")],
        "quem": "zoe",
        "texto": ("E hoje eu tenho um convidado especial aqui comigo no "
                  "estúdio. Mason, seja muito bem-vindo!"),
    },
    {
        "nota": "he returns the greeting, looking at her",
        "camera": 2,
        "seg": 1.8,
        "olhar": [("mason", "zoe")],
        "quem": "mason",
        "texto": "Obrigado, Zoe. É um prazer estar aqui.",
    },
    {
        "nota": "he introduces himself -- close-up, gaze released to the audience",
        "camera": 2,
        "seg": 2.0,
        "olhar": [("mason", None)],
        "quem": "mason",
        "texto": ("Meu nome é Mason, eu trabalho há mais de quinze anos com "
                  "desenvolvimento de sistemas, e nos últimos anos venho me "
                  "dedicando à inteligência artificial aplicada a produtos."),
    },
    {
        "nota": "back to her: the question, looking at whoever will answer",
        "camera": 3,
        "seg": 2.0,
        "olhar": [("zoe", "mason")],
        "quem": "zoe",
        "texto": ("Que ótimo. Mason, me conta uma coisa: o que mudou no seu "
                  "trabalho depois que a inteligência artificial entrou de "
                  "vez no dia a dia?"),
    },
    {
        "nota": "the answer -- close-up on him, looking at her",
        "camera": 2,
        "seg": 2.0,
        "olhar": [("mason", "zoe")],
        "quem": "mason",
        "texto": ("Mudou o ritmo, principalmente. Antes a gente passava dias "
                  "num problema que hoje se resolve numa tarde. Mas eu diria "
                  "que a parte difícil continua sendo a mesma: entender o que "
                  "o usuário realmente precisa."),
        # the reaction cut lands IN THE MIDDLE of this line
        "reacao": {"camera": 3, "em": 0.45, "seg": 1.2},
    },
    {
        "nota": "closing -- back to the wide shot, both in frame",
        "camera": 1,
        "seg": 2.4,
        "olhar": [("zoe", "mason"), ("mason", "zoe")],
        "quem": "zoe",
        "texto": ("Perfeito. Fica com a gente, que já voltamos com a segunda "
                  "parte da conversa."),
    },
]


def grava(usar_ia=False):
    print("=" * 68)
    print("podcast recording")
    print("=" * 68)

    if not chama("/api/status"):
        print("The game did not respond at {}. Start it first.".format(BASE))
        return 1

    # The free camera must be on -- without it the pawn does the framing.
    chama("/api/camera?ligar=1")

    # ------------------------------------------- PHASE 1: generate it all
    #
    # Generating a line takes a few seconds (AI + TTS). Generated during
    # the recording, those seconds become silence between lines -- in an
    # 8-block episode, nearly half a minute of technical dead air.
    #
    # With everything ready BEFOREHAND, playback waits for nothing: the
    # only thing driving the rhythm is the conversation itself. It also
    # unlocks the OVERLAP, which is what makes a dialogue sound natural --
    # real people start answering before the other person finishes.
    print()
    print("-" * 68)
    print("PHASE 1 -- generating {} lines".format(len(ROTEIRO)))
    print("-" * 68)

    t_geracao = time.time()
    for i, bloco in enumerate(ROTEIRO, 1):
        arquivo, seg = prepara(bloco["quem"], bloco["texto"], usar_ia)
        if not arquivo:
            print("  ! block {} produced no audio -- aborting".format(i))
            return 1
        bloco["_arquivo"] = arquivo
        bloco["_seg"] = seg
        print("  [{}/{}] {:<6} {:>5.1f}s  {}".format(
            i, len(ROTEIRO), bloco["quem"], seg,
            bloco["texto"][:40] + ("..." if len(bloco["texto"]) > 40 else "")))

    total = sum(b["_seg"] for b in ROTEIRO)
    print()
    print("  generated in {:.0f}s | {:.0f}s of speech".format(
        time.time() - t_geracao, total))

    # --------------------------------------- PHASE 2: record the episode
    print()
    print("-" * 68)
    print("PHASE 2 -- recording")
    print("-" * 68)

    camera_atual = None
    for i, bloco in enumerate(ROTEIRO):
        print()
        print("[{}/{}] {}".format(i + 1, len(ROTEIRO), bloco["nota"]))
        print('      {}: "{}"'.format(
            bloco["quem"],
            bloco["texto"][:58] + ("..." if len(bloco["texto"]) > 58 else "")))

        # 1) THE CAMERA: travel on the OPENING, hard cut everywhere else.
        #
        # A travel between lines creates a silent gap; a travel during the
        # previous line puts visible camera motion under someone speaking.
        # Both violate the grammar. The broadcast answer is the CUT: it is
        # instantaneous, so it can happen exactly on the turn change --
        # no silence AND no visible motion. The travel stays only on the
        # opening, where touring the studio is the point of the shot.
        if i == 0:
            print("      camera -> angle {} (opening travel)"
                  .format(bloco["camera"]))
            camera(bloco["camera"], bloco.get("seg"))
            espera_camera()
            time.sleep(0.6)
        elif bloco["camera"] != camera_atual:
            print("      cut -> angle {}".format(bloco["camera"]))
            corta(bloco["camera"])
        camera_atual = bloco["camera"]

        # 2) RELEASE THE LINE. Already on disk, so it plays on the next
        # frame.
        real = toca(bloco["quem"], bloco["_arquivo"]) or bloco["_seg"]
        print("      speaking ({:.1f}s)".format(real))
        t0 = time.time()

        # 3) The reaction cut, at the exact point of the line -- AND BACK.
        #
        # Both ways as HARD CUTS. Using the real audio duration makes the
        # cut land where intended; estimating by characters per second
        # drifts.
        reacao = bloco.get("reacao")
        if reacao:
            # The voice continues, the image changes -- the oldest device
            # in edited interviews.
            alvo = real * reacao["em"]
            resta = alvo - (time.time() - t0)
            if resta > 0:
                time.sleep(resta)
            print("      reaction cut -> angle {} (at {:.1f}s of {:.1f}s)"
                  .format(reacao["camera"], alvo, real))
            corta(reacao["camera"])
            camera_atual = reacao["camera"]

            # The reaction is a PARENTHESIS: show the listener for ~2 s
            # and RETURN to the speaker. A reaction without the return
            # reads as the camera abandoning whoever is talking. Only
            # return if there is enough line left.
            dura = float(reacao.get("dura", 2.0))
            falta = real - (time.time() - t0)
            if falta > dura + 1.0:
                time.sleep(dura)
                print("      cut back -> angle {}".format(bloco["camera"]))
                corta(bloco["camera"])
                camera_atual = bloco["camera"]

        # 4) WAIT UNTIL THE SPLICE POINT. The next block enters
        # ANTECIPACAO seconds before this line ends (only between
        # DIFFERENT agents: overlapping your own next line would sound
        # like talking over yourself).
        proximo = ROTEIRO[i + 1] if i + 1 < len(ROTEIRO) else None
        emenda = ANTECIPACAO if (proximo and
                                 proximo["quem"] != bloco["quem"]) else 0.0

        espera = max(0.0, real - emenda - (time.time() - t0))
        if espera > 0:
            time.sleep(espera)
        if emenda:
            print("      (next line enters {:.1f}s before the end)"
                  .format(emenda))

    print()
    print("=" * 68)
    print("end of episode")
    return 0


def lista():
    print("Episode script ({} blocks):".format(len(ROTEIRO)))
    for i, b in enumerate(ROTEIRO, 1):
        olhares = ", ".join(
            "{}->{}".format(q, a if a else "camera")
            for q, a in b.get("olhar", []))
        print()
        print("  [{}] {}".format(i, b["nota"]))
        print("      angle {}  |  {}".format(b["camera"], olhares))
        print("      {}: {}".format(b["quem"], b["texto"]))
        if b.get("reacao"):
            print("      + reaction cut to angle {} at {:.0f}% of the line"
                  .format(b["reacao"]["camera"], b["reacao"]["em"] * 100))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Record a podcast episode")
    p.add_argument("--ia", action="store_true",
                   help="each line becomes a question to that agent's AI "
                        "instead of fixed text")
    p.add_argument("--listar", action="store_true",
                   help="print the episode script without recording")
    args = p.parse_args()

    sys.exit(lista() if args.listar else grava(args.ia))
