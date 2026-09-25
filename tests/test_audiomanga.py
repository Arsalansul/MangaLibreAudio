import json
import io
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

import audiomanga


class AudioMangaTests(unittest.TestCase):
    def test_write_pcm_accepts_tensor_like_values_without_numpy(self):
        class TensorLike:
            def tolist(self):
                return [-1.0, 0.0, 1.0]

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output = Path(temporary) / "tensor.wav"
            audiomanga.write_pcm16_wav(output, TensorLike(), 48000)
            self.assertEqual(audiomanga.wav_info(output), (1, 2, 48000, 3))

    def test_discover_pages_uses_translation_and_skips_missing_images(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            chapter = Path(temporary)
            (chapter / "0001.png").write_bytes(b"image")
            (chapter / "0001.analysis.json").write_text(
                json.dumps(
                    {
                        "regions": [
                            {"id": "left", "bbox": [10, 10, 20, 20], "translation": " Два "},
                            {"id": "right", "bbox": [100, 10, 20, 20], "translation": "Один"},
                            {"id": "empty", "bbox": [0, 0, 1, 1], "translation": ""},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (chapter / "0002.analysis.json").write_text('{"regions": []}', encoding="utf-8")
            pages, warnings = audiomanga.discover_pages(chapter)
            self.assertEqual([region["id"] for region in pages[0]["regions"]], ["right", "left"])
            self.assertEqual(pages[0]["regions"][1]["text"], "Два")
            self.assertEqual(len(warnings), 1)

    def test_region_order_does_not_depend_on_region_height(self):
        regions = [
            {"id": "lower-large", "bbox": [800, 533, 140, 161]},
            {"id": "upper-small", "bbox": [130, 371, 100, 7]},
        ]
        ordered = sorted(regions, key=audiomanga.region_sort_key)
        self.assertEqual([region["id"] for region in ordered], ["upper-small", "lower-large"])

    def test_make_ssml_escapes_text_and_applies_prosody(self):
        ssml = audiomanga.make_ssml(
            "Я < вижу & слышу",
            {
                "rate": "slow",
                "pitch": "low",
                "pause_before_ms": 100,
                "pause_after_ms": 250,
            },
        )
        self.assertIn('<break time="100ms"/>', ssml)
        self.assertIn('rate="slow" pitch="low"', ssml)
        self.assertIn("Я &lt; вижу &amp; слышу", ssml)
        self.assertIn('<break time="250ms"/>', ssml)

    def test_ellipsis_gets_a_slower_default_delivery(self):
        prosody = audiomanga.default_prosody("Я чувствую это...")
        self.assertEqual(prosody["rate"], "slow")
        self.assertEqual(prosody["pitch"], "low")
        self.assertGreater(prosody["pause_after_ms"], 0)

    def test_clean_f5_text_removes_silero_stress_markers(self):
        self.assertEqual(audiomanga.clean_f5_text("Ч+УВСТВУЮ ЭТО ЗДЕСЬ"), "Чувствую это здесь")
        self.assertEqual(audiomanga.clean_f5_text("Это м+ой текст"), "Это мой текст")

    def test_wav_has_speech_rejects_digital_silence(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            silent = root / "silent.wav"
            voiced = root / "voiced.wav"
            for path, frame in ((silent, b"\x03\x00"), (voiced, b"\x00\x10")):
                with wave.open(str(path), "wb") as audio:
                    audio.setnchannels(1)
                    audio.setsampwidth(2)
                    audio.setframerate(48000)
                    audio.writeframes(frame * 100)
            self.assertFalse(audiomanga.wav_has_speech(silent))
            self.assertTrue(audiomanga.wav_has_speech(voiced))

    def test_remote_f5_sends_multipart_and_saves_wav(self):
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(b"\x00\x10" * 48)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return wav_buffer.getvalue()

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            reference = root / "voice.mp3"
            reference.write_bytes(b"reference")
            output = root / "result.wav"
            with patch("audiomanga.urllib.request.urlopen", return_value=Response()) as call:
                audiomanga.synthesize_f5_remote(
                    "Привет", output, reference, "Пример", 1.0, 16, 2.0, 0.0,
                    "http://worker:8770", prosody={},
                )
            request = call.call_args.args[0]
            self.assertEqual(request.full_url, "http://worker:8770/synthesize")
            self.assertIn(b'name="text"', request.data)
            self.assertIn("multipart/form-data", request.headers["Content-type"])
            self.assertEqual(audiomanga.wav_info(output), (1, 2, 48000, 48))

    def test_apply_wav_gain_changes_pcm_amplitude(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output = Path(temporary) / "gain.wav"
            with wave.open(str(output), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(48000)
                audio.writeframes(b"\xe8\x03" * 10)  # 1000
            audiomanga.apply_wav_gain(output, 6.0)
            with wave.open(str(output), "rb") as audio:
                samples = array("h")
                samples.frombytes(audio.readframes(10))
            self.assertAlmostEqual(samples[0], 1995, delta=2)

    def test_combine_audio_adds_page_timing(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            root = Path(temporary)
            clip = root / "clip.wav"
            with wave.open(str(clip), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(1000)
                output.writeframes(b"\x00\x00" * 1000)
            combined = root / "combined.wav"
            durations = audiomanga.combine_audio(
                [[clip]],
                combined,
                {
                    "sample_rate": 1000,
                    "page_lead_ms": 100,
                    "page_tail_ms": 200,
                    "pause_between_replicas_ms": 50,
                },
            )
            self.assertAlmostEqual(durations[0], 1.3, places=3)
            self.assertEqual(audiomanga.wav_info(combined)[3], 1300)

    def test_add_wav_padding(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as temporary:
            output = Path(temporary) / "clip.wav"
            with wave.open(str(output), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(1000)
                audio.writeframes(b"\x01\x00" * 1000)
            audiomanga.add_wav_padding(output, 100, 250)
            self.assertEqual(audiomanga.wav_info(output)[3], 1350)


if __name__ == "__main__":
    unittest.main()
