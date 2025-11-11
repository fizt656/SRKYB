import requests
import os
import base64
from dotenv import load_dotenv
import json
from openai import OpenAI # Added for Chat Completions

# --- Load Environment Variables ---
load_dotenv()
API_KEY = os.getenv("OPENAI_API_KEY")

# --- Constants ---
IMAGE_API_URL = "https://api.openai.com/v1/images/generations"
PROMPTS_FILE = "prompts.json"

# --- Initialize OpenAI Client (for Chat Completions) ---
# Ensure API key is available before initializing client
if API_KEY and API_KEY != "YOUR_API_KEY_HERE":
    client = OpenAI(api_key=API_KEY)
else:
    client = None # Will be checked later

# --- Utility Functions ---
def load_prompts():
    """Loads prompt templates from the JSON file."""
    try:
        with open(PROMPTS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"Error: Prompts file '{PROMPTS_FILE}' not found.")
        return None
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON from '{PROMPTS_FILE}': {e}")
        return None

PROMPTS = load_prompts() # Load prompts when module is imported

def check_api_key():
    """Checks if the API key is available and valid."""
    global client # Allow modification if key was missing initially
    if not API_KEY or API_KEY == "YOUR_API_KEY_HERE":
        print("Error: OPENAI_API_KEY not found or not set in .env file.")
        print("Please add your API key to the .env file.")
        return False
    # Initialize client if it wasn't initialized due to missing key at import time
    if client is None:
         try:
             client = OpenAI(api_key=API_KEY)
             print("OpenAI client initialized successfully.")
         except Exception as e:
             print(f"Error initializing OpenAI client: {e}")
             return False
    return True

# --- Stage 1: Text Generation (Single Page with History) ---
def generate_single_page_structure(characters, story_outline, page_number, message_history, total_pages=10, model="gpt-4o", style_type="childrens", reference_image=None, reference_image_type=None):
    """
    Generates the structure (scene description, page text OR script text) for a single page
    using the Chat Completions API, maintaining conversational history.

    Args:
        characters (dict): Dictionary of character names and descriptions.
        story_outline (str): The user's general story outline.
        page_number (int): The current page number to generate (1-based).
        message_history (list): The list of messages sent/received so far.
        total_pages (int): The total number of pages in the book.
        model (str): The chat model to use (e.g., "gpt-4o").
        style_type (str): The type of style ('childrens' or 'narrative') to determine prompt and output structure.
        reference_image (str, optional): Base64 encoded image data. Defaults to None.
        reference_image_type (str, optional): The type of reference ('style', 'character', 'setting'). Defaults to None.

    Returns:
        dict or None: A dictionary for the single page if successful (containing 'page_text' or 'script_text'), otherwise None.
        list: The updated message history.
        str or None: An error message if unsuccessful, otherwise None.
    """
    if not check_api_key() or client is None:
        return None, message_history, "API key not configured or client not initialized."
    if not PROMPTS:
        return None, message_history, "Prompts could not be loaded."

    current_history = list(message_history) # Work with a copy

    try:
        # Select the appropriate Stage 1 prompt based on style type and reference image
        if style_type == "narrative":
            base_prompt_key = 'stage1_text_generation_narrative_page'
            expected_text_key = 'script_text'
        else: # Default to childrens
            base_prompt_key = 'stage1_text_generation_single_page'
            expected_text_key = 'page_text'

        prompt_key = base_prompt_key
        if reference_image and reference_image_type:
            ref_prompt_key = f"{base_prompt_key}_ref"
            if ref_prompt_key in PROMPTS:
                prompt_key = ref_prompt_key
                print(f"Using vision-enabled text generation prompt: {prompt_key}")
            else:
                print(f"Warning: Vision-enabled prompt '{ref_prompt_key}' not found. Falling back to standard text prompt.")
        else:
            print(f"Using standard text generation prompt: {prompt_key}")


        try:
            stage1_prompts = PROMPTS[prompt_key]
            system_msg = stage1_prompts['system_message']
            prompt_template = stage1_prompts['user_prompt_template']
        except KeyError:
             return None, message_history, f"Could not find prompt key '{prompt_key}' in prompts.json"

        # Build the user message for the current page request
        if page_number == 1:
            # Initial prompt for the first page
            characters_json_str = json.dumps(characters, indent=2)
            user_prompt_text = prompt_template.format(
                characters_json=characters_json_str,
                story_outline=story_outline,
                page_number=page_number,
                total_pages=total_pages,
                reference_image_type=reference_image_type
            )

            user_message_content = [
                {"type": "text", "text": user_prompt_text}
            ]

            if reference_image:
                user_message_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": reference_image,
                        "detail": "high"
                    }
                })

            # Start the history
            current_history = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_message_content}
            ]
        else:
            # Subsequent prompts just ask for the next page
            # The history already contains system msg, initial prompt, and previous page responses
            next_page_request = f"Now generate ONLY the JSON object for page {page_number}, continuing the story logically."
            current_history.append({"role": "user", "content": next_page_request})

        print(f"\n--- Sending request to Chat Completions API for page {page_number} structure ---")
        # print("DEBUG History:", current_history) # Optional: print history being sent

        response = client.chat.completions.create(
            model=model,
            messages=current_history, # Send the whole history
            response_format={"type": "json_object"},
            max_tokens=1000 # Should be enough for one page's JSON
        )

        content = response.choices[0].message.content
        print(f"--- Received structure response for page {page_number} ---")

        # Add the assistant's response to the history for the *next* iteration
        current_history.append({"role": "assistant", "content": content})

        # Attempt to parse the JSON content (expecting a single object)
        try:
            page_data = json.loads(content)
            # Validate the single object structure based on expected text key
            if isinstance(page_data, dict) and \
               "page_number" in page_data and \
               "scene_description" in page_data and \
               expected_text_key in page_data and \
               page_data["page_number"] == page_number:
                 print(f"--- Page {page_number} structure parsed successfully ({expected_text_key} found) ---")
                 return page_data, current_history, None # Return updated history
            else:
                 raise ValueError(f"Parsed JSON for page {page_number} has incorrect structure, missing '{expected_text_key}', or page number mismatch.")

        except (json.JSONDecodeError, ValueError) as e:
            error_msg = f"Error parsing JSON response for page {page_number}: {e}"
            print(error_msg)
            print("Raw Content:", repr(content))
            return None, current_history, error_msg # Return history even on error

    except Exception as e:
        error_msg = f"Error during Chat Completions API request for page {page_number}: {e}"
        print(error_msg)
        return None, current_history, error_msg # Return history even on error


# --- Stage 2: Image Generation ---
def generate_image_from_prompt(prompt_text, size="1536x1024", quality="high"):
    """
    Generates an image using the OpenAI Images API based on the provided prompt.

    Args:
        prompt_text (str): The detailed text prompt for the image generation.
        size (str): The desired image size (e.g., "1536x1024", "1024x1024").
        quality (str): The desired image quality ("low", "medium", "high", "auto").

    Returns:
        bytes or None: The image data as bytes if successful, otherwise None.
        str or None: An error message if unsuccessful, otherwise None.
    """
    if not check_api_key():
        return None, "API key not configured."

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    payload = {
        "model": "gpt-image-1", # Hardcoded for this project
        "prompt": prompt_text,
        "n": 1,
        "size": size,
        "quality": quality,
        "moderation": "low" # Added to potentially bypass safety system blocks
        # "response_format": "b64_json" # REMOVED: Not supported by gpt-image-1
    }

    print(f"\n--- Sending request to OpenAI Images API ---")
    print(f"Prompt: {prompt_text}") # Added print statement for prompt
    # print(f"DEBUG Image Prompt: {repr(prompt_text)}") # Keep commented out unless needed

    try:
        response = requests.post(IMAGE_API_URL, headers=headers, json=payload)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        response_data = response.json()

        # Check response structure (gpt-image-1 specific)
        if "data" in response_data and len(response_data["data"]) > 0 and "b64_json" in response_data["data"][0]:
            b64_image_data = response_data["data"][0]["b64_json"]
            image_bytes = base64.b64decode(b64_image_data)
            print("--- Image successfully generated ---")
            # Optionally print usage info if available (gpt-image-1 provides this)
            if "usage" in response_data:
                print(f"Image API Usage Info: {response_data['usage']}")
            return image_bytes, None
        else:
            error_msg = "Error: Unexpected Images API response format. 'b64_json' not found."
            print(error_msg)
            print("Full Response:", json.dumps(response_data, indent=2))
            return None, error_msg

    except requests.exceptions.RequestException as e:
        error_msg = f"Error during Images API request: {e}"
        print(error_msg)
        if e.response is not None:
            status_code = e.response.status_code
            print(f"Status Code: {status_code}")
            # Prepend specific message for potential moderation blocks
            if status_code == 400:
                error_msg = f"[Potential Moderation Error] {error_msg}"
            try:
                error_details = json.dumps(e.response.json(), indent=2)
                print("Error Response:", error_details)
                error_msg += f"\nDetails: {error_details}"
            except json.JSONDecodeError:
                error_details = e.response.text
                print("Error Response (non-JSON):", error_details)
                error_msg += f"\nDetails: {error_details}"
        return None, error_msg

    except Exception as e:
        error_msg = f"An unexpected error occurred during image generation: {e}"
        print(error_msg)
        return None, error_msg

# --- Stage 2: Image Editing ---
def edit_image_from_prompt(previous_image_data, prompt_text, size="1536x1024", quality="high"):
    """
    Edits an image using the OpenAI Images API (gpt-image-1) based on the provided prompt.

    Args:
        previous_image_data (bytes): The image data of the previous page.
        prompt_text (str): The detailed text prompt for the image editing.
        size (str): The desired image size (e.g., "1536x1024", "1024x1024").
        quality (str): The desired image quality ("low", "medium", "high", "auto").

    Returns:
        bytes or None: The edited image data as bytes if successful, otherwise None.
        str or None: An error message if unsuccessful, otherwise None.
    """
    if not check_api_key():
        return None, "API key not configured."

    headers = {
        "Authorization": f"Bearer {API_KEY}"
    }

    # The /v1/images/edits endpoint requires multipart/form-data
    # We need to send the image data as a file-like object

    # Create a file-like object from bytes
    import io
    image_file = io.BytesIO(previous_image_data)
    image_file.name = "previous_image.png" # Give it a name, extension might matter

    files = {
        "image": image_file
    }

    data = {
        "model": "gpt-image-1", # Hardcoded for this project
        "prompt": prompt_text,
        "n": 1,
        "size": size,
        "quality": quality,
        # "moderation": "low" # Optional: uncomment if needed
        # "response_format": "b64_json" # REMOVED: Not supported by gpt-image-1
    }

    print(f"\n--- Sending request to OpenAI Images API (Edit - gpt-image-1) ---")
    print(f"Prompt: {prompt_text}") # Added print statement for prompt
    # print(f"DEBUG Edit Prompt: {repr(prompt_text)}") # Keep commented out unless needed

    try:
        # Use requests.post with files and data for multipart/form-data
        response = requests.post("https://api.openai.com/v1/images/edits", headers=headers, files=files, data=data)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        response_data = response.json()

        # Check response structure (gpt-image-1 specific)
        if "data" in response_data and len(response_data["data"]) > 0 and "b64_json" in response_data["data"][0]:
            b64_image_data = response_data["data"][0]["b64_json"]
            image_bytes = base64.b64decode(b64_image_data)
            print("--- Image successfully edited ---")
            # Optionally print usage info if available (gpt-image-1 provides this)
            if "usage" in response_data:
                print(f"Image API Usage Info: {response_data['usage']}")
            return image_bytes, None
        else:
            error_msg = "Error: Unexpected Images API (Edit) response format. 'b64_json' not found."
            print(error_msg)
            print("Full Response:", json.dumps(response_data, indent=2))
            return None, error_msg

    except requests.exceptions.RequestException as e:
        error_msg = f"Error during Images API (Edit) request: {e}"
        print(error_msg)
        if e.response is not None:
            status_code = e.response.status_code
            print(f"Status Code: {status_code}")
            # Prepend specific message for potential moderation blocks
            if status_code == 400:
                error_msg = f"[Potential Moderation Error] {error_msg}"
            try:
                error_details = json.dumps(e.response.json(), indent=2)
                print("Error Response:", error_details)
                error_msg += f"\nDetails: {error_details}"
            except json.JSONDecodeError:
                error_details = e.response.text
                print("Error Response (non-JSON):", error_details)
                error_msg += f"\nDetails: {error_details}"
        return None, error_msg

    except Exception as e:
        error_msg = f"An unexpected error occurred during image editing: {e}"
        print(error_msg)
        return None, error_msg


# --- Character Inference ---
def infer_characters(story_concept, model="gpt-4o", reference_image=None, reference_image_type=None):
    """
    Infers potential characters and descriptions based on a story concept using the Chat Completions API.
    If a reference image for characters is provided, it uses vision to describe the character.

    Args:
        story_concept (str): A brief description of the story's theme or plot.
        model (str): The chat model to use (e.g., "gpt-4o").
        reference_image (str, optional): Base64 encoded image data. Defaults to None.
        reference_image_type (str, optional): The type of reference ('style', 'character', 'setting'). Defaults to None.

    Returns:
        dict or None: A dictionary mapping character names to descriptions if successful, otherwise None.
        str or None: An error message if unsuccessful, otherwise None.
    """
    if not check_api_key() or client is None:
        return None, "API key not configured or client not initialized."

    system_message = "You are an assistant skilled at identifying key characters from a story concept or an image and providing brief visual descriptions suitable for an illustrator."
    user_message_content = []

    if reference_image and reference_image_type == 'character':
        print("--- Using vision-based character inference ---")
        user_prompt_text = """Analyze the provided image. Identify the main character or characters.
Provide a detailed physical description for each character you identify.
Invent a suitable name for each character.
Output ONLY a single, valid JSON object mapping the invented character names (string keys) to their detailed descriptions (string values).
Example format:
{
  "Character Name 1": "Detailed visual description from image...",
  "Character Name 2": "Detailed visual description from image..."
}"""
        user_message_content.extend([
            {"type": "text", "text": user_prompt_text},
            {"type": "image_url", "image_url": {"url": reference_image, "detail": "high"}}
        ])
    else:
        print("--- Using text-based character inference ---")
        user_prompt_text = f"""Analyze the following story concept and identify 2-4 main characters that would likely appear. For each character, provide a concise visual description (appearance, notable features, clothing style if relevant).

Story Concept: "{story_concept}"

Output ONLY a single, valid JSON object mapping character names (string keys) to their descriptions (string values). Example format:
{{
  "Character Name 1": "Brief visual description...",
  "Character Name 2": "Brief visual description..."
}}"""
        user_message_content.append({"type": "text", "text": user_prompt_text})


    try:
        print("\n--- Sending request to Chat Completions API for character inference ---")
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message_content}
            ],
            response_format={"type": "json_object"},
            max_tokens=500 # Should be enough for a few character descriptions
        )

        content = response.choices[0].message.content
        print("--- Received character inference response ---")

        try:
            character_data = json.loads(content)
            # Basic validation: check if it's a dictionary with string values
            if isinstance(character_data, dict) and all(isinstance(v, str) for v in character_data.values()):
                print("--- Character data parsed successfully ---")
                if not character_data:
                     print("Warning: No characters were inferred.")
                return character_data, None
            else:
                raise ValueError("Parsed JSON is not a dictionary mapping strings to strings.")

        except (json.JSONDecodeError, ValueError) as e:
            error_msg = f"Error parsing JSON response for character inference: {e}"
            print(error_msg)
            print("Raw Content:", repr(content))
            return None, error_msg

    except Exception as e:
        error_msg = f"Error during Chat Completions API request for character inference: {e}"
        print(error_msg)
        return None, error_msg


# Example usage / test block (optional, can be removed or enhanced)
if __name__ == '__main__':
    print("Testing openai_api module...")
    if not check_api_key():
        print("Cannot run tests without a valid API key in .env")
    elif not PROMPTS:
         print("Cannot run tests without prompts.json")
    else:
        # Test Stage 1 (Single Page with History)
        print("\n--- Testing Stage 1: Single Page Text Generation (Page 1 & 2) ---")
        test_chars = {"Leo": "A curious little lion cub with a small brown mane."}
        test_outline = "Leo the lion cub explores the jungle and makes a new friend."
        history = []
        page1_data, history, error1 = generate_single_page_structure(test_chars, test_outline, page_number=1, message_history=history)

        if error1:
            print(f"Stage 1 Test Failed (Page 1): {error1}")
        elif page1_data:
            print(f"Stage 1 Test Success (Page 1): Generated structure for page {page1_data.get('page_number')}.")
            # print(json.dumps(page1_data, indent=2))

            # Test Page 2 using history
            page2_data, history, error1_2 = generate_single_page_structure(test_chars, test_outline, page_number=2, message_history=history)
            if error1_2:
                 print(f"Stage 1 Test Failed (Page 2): {error1_2}")
            elif page2_data:
                 print(f"Stage 1 Test Success (Page 2): Generated structure for page {page2_data.get('page_number')}.")
                 # print(json.dumps(page2_data, indent=2))

                 # Test Stage 2 with the first page data
                 print("\n--- Testing Stage 2: Image Generation (Page 1) ---")
                 scene_desc = page1_data.get('scene_description', '')
                 page_text = page1_data.get('page_text', '')
                 # Format character details string for the prompt
                 char_details_str = "\n".join([f"- {name}: {desc}" for name, desc in test_chars.items()])

                 # Format the image prompt using the template
                 try:
                     img_prompt_template = PROMPTS['stage2_image_generation']['prompt_template']
                     img_prompt = img_prompt_template.format(
                         scene_description=scene_desc,
                         character_details_string=char_details_str,
                         page_text=page_text
                     )

                     print(f"Generating test image for page 1...")
                     img_data, error2 = generate_image_from_prompt(img_prompt, size="1024x1024", quality="medium")

                     if error2:
                         print(f"Stage 2 Test Failed: {error2}")
                     elif img_data:
                         try:
                             with open("test_page_01.png", "wb") as f:
                                 f.write(img_data)
                             print("Stage 2 Test Success: Image saved as test_page_01.png")
                         except IOError as e:
                             print(f"Error saving test image: {e}")
                 except KeyError as e:
                      print(f"Error: Missing key in prompts.json for Stage 2: {e}")
                 except Exception as e:
                      print(f"An unexpected error occurred during Stage 2 test: {e}")
            else:
                 print("Stage 1 Test (Page 2) returned no data.")
        else:
             print("Stage 1 Test (Page 1) returned no data.")
