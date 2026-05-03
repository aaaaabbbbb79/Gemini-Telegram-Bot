from google.genai import types

conf = {
    "error_info":           "⚠️⚠️⚠️\nSomething went wrong !\nplease try to change your prompt or contact the admin !",
    "quota_error_info":     "Gemini request quota exceeded. Please try again later or choose another model with /model.",
    "auth_error_info":      "Gemini authentication failed. Please contact the bot administrator.",
    "timeout_error_info":   "Gemini request timed out. Please try again later.",
    "invalid_error_info":   "Gemini rejected this request. Please try changing your prompt or choose another model with /model.",
    "before_generate_info": "🤖Generating🤖",
    "download_pic_notify":  "🤖Loading picture🤖",
    "streaming_update_interval": 0.5,  # Streaming answer update interval (seconds)
    "max_history_turns":    20,
}

safety_settings = [
    types.SafetySetting(
        category="HARM_CATEGORY_HARASSMENT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_HATE_SPEECH",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_DANGEROUS_CONTENT",
        threshold="BLOCK_NONE",
    ),
    types.SafetySetting(
        category="HARM_CATEGORY_CIVIC_INTEGRITY",
        threshold="BLOCK_NONE",
    )
]
