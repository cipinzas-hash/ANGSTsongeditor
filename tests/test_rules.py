"""Pruebas de las reglas generales. Cada caso real encontrado en la biblioteca
queda aca como fixture: una mejora futura no puede romperlo sin que se note.
Correr: python -m unittest discover -s tests -v"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
os.environ.setdefault("DISCOGS_TOKEN", "x")
os.environ.setdefault("GEMINI_API_KEY", "x")

import resolve as R        # noqa: E402
import tagio               # noqa: E402
import tag_with_discogs as T  # noqa: E402
from pathlib import Path   # noqa: E402

DEST = "/MEGA/musica/untitledless"


def lib(*rels):
    return [f"{DEST}/{r}" for r in rels]


class Placeholders(unittest.TestCase):
    def test_placeholders_clasicos(self):
        for v in ("Unknown Artist", "<unknown>", "", None, "Desconocido"):
            self.assertTrue(tagio.is_placeholder("artist", v), v)
        self.assertTrue(tagio.is_placeholder("album", "Unknown Disc"))
        self.assertTrue(tagio.is_placeholder("album", "Untitled album"))
        self.assertTrue(tagio.is_placeholder("title", "Track 01"))

    def test_dominio_como_artista_es_basura(self):
        # caso real: artista "SonidosMp3Gratis.com"
        self.assertTrue(tagio.is_placeholder("artist", "SonidosMp3Gratis.com"))
        self.assertTrue(tagio.is_placeholder("album", "www.hotplayer.ru"))
        self.assertFalse(tagio.is_placeholder("artist", "Code 64"))
        self.assertFalse(tagio.is_placeholder("artist", "Dr. Dre"))


class Nombres(unittest.TestCase):
    def test_marcas_de_sitio_y_bitrate(self):
        self.assertEqual(R.clean_name_junk("#4 LOVE LIKE BLOOD Copycat (320 kbps)"), "LOVE LIKE BLOOD Copycat")
        self.assertEqual(R.clean_name_junk("Die Antword - I Fink U Freeky (www.hotplayer.ru)"), "Die Antword - I Fink U Freeky")

    def test_separadores(self):
        a, t, sep = T.parse_filename_ex(Path("Die Antword - I Fink U Freeky (www.hotplayer.ru).mp3"), None)
        self.assertEqual((a, t, sep), ("Die Antword", "I Fink U Freeky", True))
        a, t, sep = T.parse_filename_ex(Path("01. Boris – Furui Kokoro.mp3"), None)   # raya larga + numero
        self.assertEqual((a, t, sep), ("Boris", "Furui Kokoro", True))
        a, t, sep = T.parse_filename_ex(Path("Artist_-_Title.mp3"), None)
        self.assertEqual((a, t, sep), ("Artist", "Title", True))

    def test_sin_separador_no_inventa_artista(self):
        a, t, sep = T.parse_filename_ex(Path("#4 LOVE LIKE BLOOD Copycat (320 kbps).mp3"), None)
        self.assertFalse(sep)
        self.assertIsNone(a)

    def test_sanitizar_nombres(self):
        self.assertEqual(R.sanitize_component("Aural Vampire III: Border of the Dead"), "Aural Vampire III - Border of the Dead")
        self.assertEqual(R.sanitize_component("Dope Stars Inc."), "Dope Stars Inc")
        self.assertEqual(R.sanitize_component("What?*"), "What")
        self.assertEqual(R.sanitize_filename("DISCO4 :: PART II.mp3"), "DISCO4 - PART II.mp3")
        self.assertEqual(R.sanitize_component("Normal Name"), "Normal Name")


class TituloCopiaDelArchivo(unittest.TestCase):
    def test_titulo_que_es_el_nombre_crudo(self):
        # casos reales: el tag de titulo era el nombre de archivo
        self.assertTrue(R.title_is_raw_filename("code-64-dawn", "code-64-dawn"))
        self.assertTrue(R.title_is_raw_filename("#4 LOVE LIKE BLOOD Copycat (320 kbps)", "#4 LOVE LIKE BLOOD Copycat (320 kbps)"))
        self.assertTrue(R.title_is_raw_filename("Die Antword - I Fink U Freeky (www.hotplayer.ru)", "Die Antword - I Fink U Freeky (www.hotplayer.ru)"))

    def test_un_titulo_real_que_se_llama_igual_que_el_archivo(self):
        self.assertFalse(R.title_is_raw_filename("Intro", "Intro"))
        self.assertFalse(R.title_is_raw_filename("x-ray", "x-ray"))
        self.assertFalse(R.title_is_raw_filename("Furui Kokoro", "Boris - Furui Kokoro"))


class Romanizacion(unittest.TestCase):
    def test_deteccion_cualquier_escritura(self):
        for v in ("Кино", "Хөх Тэнгэр", "ᠮᠣᠩᠭᠣᠯ", "我的", "ﾎﾞﾘｽ", "안녕", "Արամ", "საქართველო", "বাংলা", "བོད", "አማርኛ", "ខ្មែរ", "μ-Ziq"):
            self.assertTrue(T.contains_non_latin_script(v), v)
        for v in ("Mötley Crüe", "ＡＢＣ", "♥ ★", "1º Lugar", "µ-Ziq", "Beyoncé", "", None):
            self.assertFalse(T.contains_non_latin_script(v), v)

    def test_sin_diacriticos(self):
        self.assertEqual(T.strip_diacritics("Qílǐ de háizi"), "Qili de haizi")
        self.assertEqual(T.strip_diacritics("Ōsaka"), "Osaka")


class Conocimiento(unittest.TestCase):
    def setUp(self):
        self.k = R.Knowledge.from_paths(lib(
            "Code 64/Broken Rhythm/code-64-contract-and-expand.mp3",
            "Code 64/Broken Rhythm/code-64-leaving-earth.mp3",
            "Lacrimosa/Inferno/Lacrimosa - Copycat.mp3",
            "Lacrimosa/Elodia/Ich verlasse heut Dein Herz.mp3",
            "Lacrimosa/Cover-up/#3 YENZ LEONHARDT Schakal (320 kbps).mp3",
            "Lacrimosa/Cover-up/#5 CANTERRA Alleine zu zweit (320 kbps).mp3",
            "Lacrimosa/Cover-up/#9 LORD OF THE LOST Stolzes Herz (320 kbps).mp3",
            "Unknown Artist/Unknown Disc/x.mp3",
        ), DEST)

    def test_slug_con_prefijo_de_artista_conocido(self):
        # caso real: code-64-dawn en Unknown Artist/Unknown Disc
        h = R.resolve_hints("code-64-dawn", self.k)
        self.assertEqual((h["artist"], h["title"], h["source"]), ("Code 64", "Dawn", "slug_biblioteca"))
        h = R.resolve_hints("code-64-in-your-arms", self.k)
        self.assertEqual(h["title"], "In Your Arms")

    def test_hermanos_por_patron_de_nombre(self):
        # caso real: "#4 LOVE LIKE BLOOD Copycat (320 kbps)" -> mismo patron que el disco Cover-up
        h = R.resolve_hints("#4 LOVE LIKE BLOOD Copycat (320 kbps)", self.k)
        self.assertEqual((h["artist"], h["album"], h["source"]), ("Lacrimosa", "Cover-up", "hermanos_patron+titulo"))
        self.assertEqual(h["title"], "Copycat")
        self.assertEqual(h["performer"], "LOVE LIKE BLOOD")

    def test_no_adivina_si_los_hermanos_no_son_unanimes(self):
        k = R.Knowledge.from_paths(lib(
            "A/Uno/#1 X Foo (320 kbps).mp3", "A/Uno/#2 Y Bar (320 kbps).mp3",
            "B/Dos/#1 Z Baz (320 kbps).mp3", "B/Dos/#2 W Qux (320 kbps).mp3"), DEST)
        self.assertIsNone(R.resolve_hints("#3 Q Algo (320 kbps)", k))

    def test_firma_debil_sola_no_alcanza(self):
        # solo "(N kbps)" al final, sin '#N' ni titulo conocido: demasiado comun para decidir
        k = R.Knowledge.from_paths(lib("A/Uno/Foo (320 kbps).mp3", "A/Uno/Bar (320 kbps).mp3"), DEST)
        self.assertIsNone(R.resolve_hints("Algo Nuevo (320 kbps)", k))

    def test_el_estilo_slug_solo_no_atribuye_artista(self):
        # que un disco use nombres-slug no significa que todo archivo-slug sea suyo
        k = R.Knowledge.from_paths(lib("Code 64/X/code-64-a.mp3", "Code 64/X/code-64-b.mp3"), DEST)
        self.assertIsNone(R.resolve_hints("otra-banda-cancion-rara", k))

    def test_gana_el_artista_mas_largo(self):
        k = R.Knowledge.from_paths(lib("Air/Moon Safari/air-sexy-boy.mp3", "Air Supply/Hits/air-supply-lost-in-love.mp3"), DEST)
        h = R.resolve_hints("air-supply-all-out-of-love", k)
        self.assertEqual(h["artist"], "Air Supply")

    def test_sin_evidencia_no_hay_pista(self):
        self.assertIsNone(R.resolve_hints("track-quien-sabe", self.k))

    def test_carpetas_unknown_no_ensenan_nada(self):
        self.assertNotIn("unknown-artist", self.k.artists)


class Firmas(unittest.TestCase):
    def test_firma_legible_agrupa_casos(self):
        self.assertEqual(R.readable_signature("#4 LOVE LIKE BLOOD Copycat (320 kbps)"),
                         R.readable_signature("#8 SOME OTHER BAND Another Song (192 kbps)"))


if __name__ == "__main__":
    unittest.main()
