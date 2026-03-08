import threading
import numpy as np
import pyaudio
import webrtcvad
import wave
import os
import datetime
from collections import namedtuple
from queue import Queue, Empty
from scipy import signal

from config_manager import ConfigManager
from event_bus import EventBus
from enums import RecordingMode, AudioManagerState
from profile import Profile

RecordingContext = namedtuple('RecordingContext', ['profile', 'session_id'])


class AudioManager:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.state = AudioManagerState.STOPPED
        self.recording_queue = Queue()
        self.thread = None
        self.pyaudio = pyaudio.PyAudio()
        self.debug_recording_dir = 'debug_audio'
        os.makedirs(self.debug_recording_dir, exist_ok=True)

    def start(self):
        if self.state == AudioManagerState.STOPPED:
            self.state = AudioManagerState.IDLE
            self.thread = threading.Thread(target=self._audio_thread)
            self.thread.start()

    def stop(self):
        if self.state != AudioManagerState.STOPPED:
            self.state = AudioManagerState.STOPPED
            self.recording_queue.put(None)  # Sentinel value to stop the thread
            if self.thread:
                self.thread.join(timeout=2)
                if self.thread.is_alive():
                    ConfigManager.log_print("Warning: Audio thread did not terminate gracefully.")
        self.pyaudio.terminate()

    def start_recording(self, profile: Profile, session_id: str):
        self.recording_queue.put(RecordingContext(profile, session_id))

    def stop_recording(self):
        self.recording_queue.put(None)  # Sentinel value to stop current recording

    def is_recording(self):
        return self.state == AudioManagerState.RECORDING

    def _audio_thread(self):
        while self.state != AudioManagerState.STOPPED:
            try:
                context = self.recording_queue.get(timeout=0.2)
                if context is None:
                    continue  # Skip this iteration, effectively stopping the current recording
                self.state = AudioManagerState.RECORDING
                self._record_audio(context)
                if self.state != AudioManagerState.STOPPED:
                    self.state = AudioManagerState.IDLE
            except Empty:
                continue

    def _record_audio(self, context: RecordingContext):
        recording_options = ConfigManager.get_section('recording_options', context.profile.name)
        audio_config = self._prepare_audio_config(context, recording_options)

        stream = self._setup_audio_stream(audio_config)
        debug_wav_file = (self._setup_debug_file(context, audio_config) if
                          audio_config['save_debug_audio'] else None)

        try:
            recording, speech_detected = self._capture_audio(context, audio_config,
                                                             stream, debug_wav_file)
        finally:
            self._cleanup_audio_resources(stream, debug_wav_file)

        if not context.profile.is_streaming:
            self._process_non_streaming_audio(context, audio_config, recording, speech_detected)

        context.profile.audio_queue.put(None)  # Push sentinel value

        # Notify ApplicationController of automatic termination due to silence detected by VAD
        if audio_config['use_vad'] and self.state != AudioManagerState.STOPPED:
            self.event_bus.emit("recording_stopped", context.session_id)

    def _prepare_audio_config(self, context: RecordingContext, recording_options):
        sample_rate = recording_options.get('sample_rate', 16000)
        streaming_chunk_size = context.profile.streaming_chunk_size or 4096
        frame_size = self._calculate_frame_size(sample_rate, streaming_chunk_size,
                                                context.profile.is_streaming)
        silence_duration_ms = recording_options.get('silence_duration', 900)
        recording_mode = RecordingMode[recording_options.get('recording_mode',
                                                             'PRESS_TO_TOGGLE').upper()]

        return {
            'sample_rate': sample_rate,
            'gain': recording_options.get('gain', 1.0),
            'channels': 1,
            'streaming_chunk_size': streaming_chunk_size,
            'frame_size': frame_size,
            'silence_frames': int(silence_duration_ms / (frame_size / sample_rate * 1000)),
            'sound_device': self._get_sound_device(recording_options.get('sound_device')),
            'save_debug_audio': recording_options.get('save_debug_audio', False),
            'use_vad': recording_mode in (RecordingMode.VOICE_ACTIVITY_DETECTION,
                                          RecordingMode.CONTINUOUS)
        }

    def _setup_audio_stream(self, audio_config):
        return self.pyaudio.open(format=pyaudio.paFloat32,
                                 channels=audio_config['channels'],
                                 rate=audio_config['sample_rate'],
                                 input=True,
                                 input_device_index=audio_config['sound_device'],
                                 frames_per_buffer=audio_config['frame_size'])

    def _setup_debug_file(self, context, audio_config):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{context.profile.name}_{timestamp}.wav"
        debug_wav_file = wave.open(os.path.join(self.debug_recording_dir, filename), 'wb')
        debug_wav_file.setnchannels(audio_config['channels'])
        debug_wav_file.setsampwidth(2)  # 16-bit audio
        debug_wav_file.setframerate(audio_config['sample_rate'])
        return debug_wav_file

    def _capture_audio(self, context, audio_config, stream, debug_wav_file):
        recording = []
        silent_frame_count = 0
        speech_detected = False
        sample_rate = audio_config['sample_rate']
        # Skip running VAD for the initial 0.15 seconds to avoid mistaking keyboard noise for voice
        initial_frames_to_skip = int(0.15 * sample_rate / audio_config['frame_size'])
        vad = webrtcvad.Vad(2) if audio_config['use_vad'] else None

        while self.state != AudioManagerState.STOPPED and self.recording_queue.empty():
            try:
                frame = stream.read(audio_config['frame_size'], exception_on_overflow=False)
            except Exception as e:
                ConfigManager.log_print(f"Error reading audio: {e}")
                break
            frame_array = self._process_audio_frame(frame, audio_config['gain'])
            recording.extend(frame_array)

            if debug_wav_file:
                int16_frame = (frame_array * 32767).astype(np.int16)
                debug_wav_file.writeframes(int16_frame.tobytes())

            if context.profile.is_streaming:
                self._handle_streaming(context, audio_config, recording)

            if vad:
                if initial_frames_to_skip > 0:
                    initial_frames_to_skip -= 1
                    continue
                # Convert to int16 for VAD
                int16_frame = (frame_array * 32767).astype(np.int16)
                if vad.is_speech(int16_frame.tobytes(), sample_rate):
                    silent_frame_count = 0
                    if not speech_detected:
                        ConfigManager.log_print("Speech detected.")
                        speech_detected = True
                else:
                    silent_frame_count += 1

                if speech_detected and silent_frame_count > audio_config['silence_frames']:
                    break

        return recording, speech_detected

    def _handle_streaming(self, context, audio_config, recording):
        chunk_size = audio_config['streaming_chunk_size']
        sample_rate = audio_config['sample_rate']
        while len(recording) >= chunk_size:
            # Extract a full chunk
            chunk = np.array(recording[:chunk_size], dtype=np.float32)

            # Send the chunk for processing
            self._push_audio_chunk(context, chunk, sample_rate, audio_config['channels'])

            # Remove the processed chunk from the recording
            del recording[:chunk_size]
        # At this point, 'recording' contains less than a full chunk,
        # which will be processed in the next iteration

    def _cleanup_audio_resources(self, stream, debug_wav_file):
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass

        if debug_wav_file:
            debug_wav_file.close()

    def _process_non_streaming_audio(self, context, audio_config, recording, speech_detected):
        audio_data = np.array(recording, dtype=np.float32)
        duration = len(audio_data) / audio_config['sample_rate']

        ConfigManager.log_print(f'Recording finished. Size: {audio_data.size} samples, '
                                f'Duration: {duration:.2f} seconds')

        min_duration_ms = ConfigManager.get_value(
            f'recording_options.{context.profile.name}.min_duration') or 200

        if audio_config['use_vad'] and not speech_detected:
            ConfigManager.log_print('Discarded because no speech has been detected.')
            self.event_bus.emit("audio_discarded", context.session_id)
        elif (duration * 1000) >= min_duration_ms:
            self._push_audio_chunk(context, audio_data,
                                   audio_config['sample_rate'], audio_config['channels'])
        else:
            ConfigManager.log_print('Discarded due to being too short.')
            self.event_bus.emit("audio_discarded", context.session_id)

    def _calculate_frame_size(self, sample_rate: int, streaming_chunk_size: int,
                              is_streaming: bool) -> int:
        if is_streaming:
            valid_frame_durations = [10, 20, 30]  # in milliseconds, accepted by webrtcvad
            for duration in sorted(valid_frame_durations, reverse=True):
                frame_size = int(sample_rate * (duration / 1000.0))
                if streaming_chunk_size % frame_size == 0:
                    return frame_size
            return int(sample_rate * 0.01)  # default to 10ms if no perfect divisor found
        else:
            return int(sample_rate * 0.03)  # 30ms for non-streaming

    def _get_sound_device(self, device):
        def get_default_input_device_index():
            return self.pyaudio.get_default_input_device_info()['index']

        def get_device_info(index):
            info = self.pyaudio.get_device_info_by_index(index)
            host_api = self.pyaudio.get_host_api_info_by_index(info['hostApi'])['name']
            return f"{info['name']} - {host_api}"

        if device == '' or device is None:
            default_index = get_default_input_device_index()
            device_info = get_device_info(default_index)
            ConfigManager.log_print(f"Using default input device: {device_info} "
                                    f"(index: {default_index})")
            return default_index

        try:
            device_index = int(device)
            device_info = get_device_info(device_index)
            ConfigManager.log_print(f"Using specified input device: {device_info} "
                                    f"(index: {device_index})")
            return device_index
        except (ValueError, TypeError):
            # If not an integer, try to find by name
            for i in range(self.pyaudio.get_device_count()):
                info = self.pyaudio.get_device_info_by_index(i)
                if device in info['name'] and info['maxInputChannels'] > 0:
                    device_info = get_device_info(i)
                    ConfigManager.log_print(f"Found and using input device by name: {device_info} "
                                            f"(index: {i})")
                    return i
            
            ConfigManager.log_print(f"Could not find device by name or index: {device}. Using default.")
            default_index = get_default_input_device_index()
            device_info = get_device_info(default_index)
            ConfigManager.log_print(f"Selected default input device: {device_info} "
                                    f"(index: {default_index})")
            return default_index
        except IOError:
            ConfigManager.log_print(f"Invalid device index: {device}. Using default.")
            default_index = get_default_input_device_index()
            device_info = get_device_info(default_index)
            ConfigManager.log_print(f"Selected default input device: {device_info} "
                                    f"(index: {default_index})")
            return default_index

    def _process_audio_frame(self, frame: bytes, gain: float) -> np.ndarray:
        frame_array = np.frombuffer(frame, dtype=np.float32).copy()
        frame_array *= gain
        np.clip(frame_array, -1.0, 1.0, out=frame_array)
        return frame_array

    def _push_audio_chunk(self, context: RecordingContext, audio_data: np.ndarray,
                          sample_rate: int, channels: int):
        target_sample_rate = 16000
        if sample_rate != target_sample_rate:
            # Resample audio to 16000Hz
            num_samples = int(len(audio_data) * target_sample_rate / sample_rate)
            audio_data = signal.resample_poly(audio_data, target_sample_rate, sample_rate)
            sample_rate = target_sample_rate

        context.profile.audio_queue.put({
            'session_id': context.session_id,
            'sample_rate': sample_rate,
            'channels': channels,
            'language': 'auto',
            'audio_chunk': audio_data
        })

    def cleanup(self):
        self.stop()
        self.thread = None
        self.pyaudio = None
        self.recording_queue = None

