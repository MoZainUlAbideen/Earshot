"""Local Whisper (faster-whisper on CPU): the offline fallback, and a neutral draft
transcriber for building eval references.

Same interface as the Groq client (FLAC bytes in, Transcription out), so it plugs
into transcribe_audio()/transcribe_episode() unchanged. On a laptop CPU without a GPU
only small models are practical; measure before relying on it.
"""

import io

from earshot.transcribe import Transcription, Word

DEFAULT_LOCAL_MODEL = "small.en"
# faster-whisper computes features for a whole chunk at once (a 600 s chunk needs a
# (1, 60000, 400) float64 array, ~183 MB, plus copies). Groq's 600 s chunks are sized for
# its upload limit; locally, memory is the limit, so use shorter chunks.
LOCAL_MAX_CHUNK_SECONDS = 120


class LocalTranscriber:
    max_chunk_seconds = LOCAL_MAX_CHUNK_SECONDS  # read by transcribe_audio()

    def __init__(self, model_size: str = DEFAULT_LOCAL_MODEL, compute_type: str = "int8", model=None):
        self.model_size = model_size
        self.compute_type = compute_type
        self._model = model  # injectable for tests; otherwise loaded on first use

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # heavy: import and load lazily

            self._model = WhisperModel(self.model_size, device="cpu", compute_type=self.compute_type)
        return self._model

    def __call__(self, flac: bytes) -> Transcription:
        segments, _info = self.model.transcribe(
            io.BytesIO(flac),
            language="en",
            word_timestamps=True,
            vad_filter=False,       # our own VAD already cut the chunk at silences
            condition_on_previous_text=False,  # reduces repetition loops (a known hallucination trigger)
        )
        words, texts = [], []
        for segment in segments:  # a generator: transcription happens while iterating
            texts.append(segment.text.strip())
            words += [Word(w.word.strip(), float(w.start), float(w.end)) for w in segment.words or []]
        return Transcription(text=" ".join(texts), words=words)
