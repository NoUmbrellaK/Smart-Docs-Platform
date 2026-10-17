"""Throwaway attribution of native loading, not a production encoder."""
import os
import runpy
import sys

# Reuse the experiment's exact setup, limits, sample, validation and reporters.
# Inject only observation wrappers at TextEmbedding construction, after imports.
source = '/tmp/smartdocs-prepacking-probe.py'
if len(sys.argv) != 5 or sys.argv[4] != '0':
    raise SystemExit('phase observation requires the production prepacking setting')


def observe(frame, event, arg):
    if event != 'call' or frame.f_code.co_name != 'retain_model':
        return
    sys.setprofile(None)
    snapshot = frame.f_globals['snapshot']
    from fastembed.common.onnx_model import OnnxModel
    from fastembed.text.onnx_text_model import OnnxTextModel
    load_session = OnnxModel._load_onnx_model
    load_tokenizer = OnnxTextModel._load_tokenizer

    def session(self, *args, **kwargs):
        snapshot('before_session')
        result = load_session(self, *args, **kwargs)
        snapshot('after_session')
        return result

    def tokenizer(self, *args, **kwargs):
        snapshot('before_tokenizer')
        result = load_tokenizer(self, *args, **kwargs)
        snapshot('after_tokenizer')
        return result

    OnnxModel._load_onnx_model = session
    OnnxTextModel._load_tokenizer = tokenizer


sys.setprofile(observe)
try:
    runpy.run_path(source, run_name='__main__')
finally:
    sys.setprofile(None)
