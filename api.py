import os
import re
import json
import time
import sys
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import uvicorn
from fastapi.middleware.cors import CORSMiddleware

# Import necessary functions from existing backend files
from openai_api import (
    generate_single_page_structure,
    generate_image_from_prompt,
    infer_characters,
    check_api_key,
    PROMPTS,
    edit_image_from_prompt
)
from replicate_api import generate_image_with_replicate, check_replicate_api_key
from utils import sanitize_filename # Assuming sanitize_filename is needed

# Define a request body model
class BookGenerationRequest(BaseModel):
    bookTitle: str
    selectedStyle: str
    numberOfPages: int
    quickMode: bool
    characterDescriptions: str | None = None # Optional for Quick Mode
    storyOutline: str
    useExperimentalConsistency: bool
    modelSelection: str
    referenceImage: str | None = None
    referenceImageType: str | None = None
    safetyTolerance: int | None = None

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # Allow requests from your Vue frontend
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (OPTIONS, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)

def get_available_styles():
    """Reads prompts.json and returns a list of available illustration styles."""
    styles = []
    # Fallback to prompts_example.json if prompts.json doesn't exist
    prompts_file = 'prompts.json' if os.path.exists('prompts.json') else 'prompts_example.json'
    try:
        with open(prompts_file, 'r') as f:
            prompts_data = json.load(f)
        for key, value in prompts_data.items():
            if key.startswith("stage2_image_") and "_ref_" not in key:
                # Attempt to get a description from the prompt, otherwise generate one
                desc = value.get("description", "Custom Style")
                styles.append({"key": key, "desc": desc})
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Error reading or parsing {prompts_file}: {e}")
        # Provide a default style as a fallback
        return [{"key": "stage2_image_childrens", "desc": "Default Dreamy Childrens Book"}]
    return styles

@app.get("/api/styles")
async def get_styles():
    """API endpoint to get the list of available illustration styles."""
    return get_available_styles()

@app.websocket("/ws/generate-progress")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("WebSocket connection accepted.")
    try:
        # Receive the book generation parameters from the frontend
        data = await websocket.receive_text()
        request = BookGenerationRequest.model_validate_json(data) # Use model_validate_json for Pydantic v2+

        print("Received book generation request via WebSocket:")
        print(f"  Book Title: {request.bookTitle}")
        print(f"  Selected Style: {request.selectedStyle}")
        print(f"  Number of Pages: {request.numberOfPages}")
        print(f"  Quick Mode: {request.quickMode}")
        if not request.quickMode:
            print(f"  Character Descriptions: {request.characterDescriptions}")
        print(f"  Story Outline: {request.storyOutline}")
        print(f"  Use Experimental Consistency: {request.useExperimentalConsistency}")

        # --- Adapt Logic from create_book.py main function ---

        # Check API Key and Prompts
        if request.modelSelection == 'openai' and not check_api_key():
            await websocket.send_text(json.dumps({"status": "error", "message": "OpenAI API key not configured."}))
            return
        if request.modelSelection == 'replicate' and not check_replicate_api_key():
            await websocket.send_text(json.dumps({"status": "error", "message": "Replicate API key not configured."}))
            return
        if not PROMPTS:
            await websocket.send_text(json.dumps({"status": "error", "message": "Could not load prompts from prompts.json."}))
            return

        # Get chosen style details (assuming selectedStyle from frontend matches a key in PROMPTS)
        style_type = "childrens" # Default
        if "narrative" in request.selectedStyle.lower():
            style_type = "narrative"

        # Infer Characters if in Quick Mode
        characters = {}
        if request.quickMode:
            await websocket.send_text(json.dumps({"status": "progress", "message": "Quick Mode: Inferring characters from story concept..."}))
            inferred_chars, char_error = infer_characters(
                request.storyOutline,
                reference_image=request.referenceImage,
                reference_image_type=request.referenceImageType
            )
            if char_error:
                await websocket.send_text(json.dumps({"status": "error", "message": f"Error inferring characters: {char_error}"}))
                return
            if not inferred_chars:
                 await websocket.send_text(json.dumps({"status": "error", "message": "Could not infer characters from the concept."}))
                 return
            characters = inferred_chars
            print("Inferred Characters:", characters)
            await websocket.send_text(json.dumps({"status": "progress", "message": "Characters inferred successfully."}))
        else:
            if request.characterDescriptions:
                try:
                    characters = json.loads(request.characterDescriptions)
                    if not isinstance(characters, dict):
                         raise ValueError("Character descriptions must be a JSON object.")
                except (json.JSONDecodeError, ValueError) as e:
                    await websocket.send_text(json.dumps({"status": "error", "message": f"Invalid character descriptions JSON: {e}"}))
                    return
            if not characters:
                 await websocket.send_text(json.dumps({"status": "error", "message": "Character descriptions are required in Full Mode."}))
                 return


        # Setup Output Directory
        sanitized_title = sanitize_filename(request.bookTitle)
        book_dir = os.path.join("output_books", sanitized_title)

        try:
            os.makedirs(book_dir, exist_ok=True)
            print(f"Output directory: {book_dir}")
            await websocket.send_text(json.dumps({"status": "progress", "message": f"Output directory created at: {book_dir}"}))
        except OSError as e:
            await websocket.send_text(json.dumps({"status": "error", "message": f"Error creating directory {book_dir}: {e}"}))
            return

        # --- Generate Cover Image ---
        await websocket.send_text(json.dumps({"status": "progress", "message": "Generating book cover..."}))
        cover_image_data = None
        try:
            cover_template = PROMPTS.get('cover_image_generation', {}).get('prompt_template')
            if not cover_template:
                 print("Warning: Cover image generation template not found. Skipping cover.")
                 await websocket.send_text(json.dumps({"status": "warning", "message": "Cover template missing, skipping cover image."}))
            else:
                all_char_details_string = "\n".join([f"- {name}: {desc}" for name, desc in characters.items()])
                cover_prompt = cover_template.format(
                    character_details_string=all_char_details_string,
                    book_title=request.bookTitle,
                    style_description=request.selectedStyle
                )
                if request.modelSelection == 'replicate':
                    cover_image_data, cover_error = generate_image_with_replicate(
                        prompt_text=cover_prompt,
                        input_image=request.referenceImage,
                        safety_tolerance=request.safetyTolerance
                    )
                else:
                    cover_image_data, cover_error = generate_image_from_prompt(
                        prompt_text=cover_prompt,
                        size="1536x1024",
                        quality="high"
                    )
                if cover_error:
                    print(f"Error generating cover image: {cover_error}")
                    await websocket.send_text(json.dumps({"status": "warning", "message": f"Error generating cover: {cover_error}. Skipping cover."}))
                    cover_image_data = None
                elif cover_image_data:
                     cover_filename = os.path.join(book_dir, "cover.png")
                     try:
                         with open(cover_filename, "wb") as f:
                             f.write(cover_image_data)
                         print(f"Cover image saved successfully as {cover_filename}")
                         await websocket.send_text(json.dumps({"status": "progress", "message": "Book cover saved successfully."}))
                     except IOError as e:
                         print(f"Error saving cover image {cover_filename}: {e}")
                         await websocket.send_text(json.dumps({"status": "warning", "message": f"Error saving cover: {e}. Continuing."}))


        except Exception as e:
            print(f"An unexpected error occurred during cover generation: {e}")
            await websocket.send_text(json.dumps({"status": "warning", "message": f"Unexpected error during cover generation: {e}. Skipping cover."}))


        # --- Loop through pages ---
        message_history = []
        previous_page_image_data = cover_image_data

        for page_num in range(1, request.numberOfPages + 1):
            progress_percent = int((page_num / request.numberOfPages) * 100)
            await websocket.send_text(json.dumps({"status": "progress", "message": f"Processing Page {page_num}/{request.numberOfPages}", "percent": progress_percent}))
            print(f"\n===== Processing Page {page_num}/{request.numberOfPages} =====")

            # --- Stage 1: Generate Single Page Structure ---
            print(f"--- Running Stage 1: Generating Structure for Page {page_num}... ---")
            page_data, message_history, error1 = generate_single_page_structure(
                characters,
                request.storyOutline,
                page_num,
                message_history,
                request.numberOfPages,
                style_type=style_type,
                reference_image=request.referenceImage,
                reference_image_type=request.referenceImageType
            )

            if error1:
                print(f"\nError generating structure for page {page_num}: {error1}")
                await websocket.send_text(json.dumps({"status": "warning", "message": f"Error generating structure for page {page_num}: {error1}. Skipping image."}))
                continue
            if not page_data:
                print(f"\nFailed to generate structure for page {page_num} (no error message).")
                await websocket.send_text(json.dumps({"status": "warning", "message": f"Failed to generate structure for page {page_num}. Skipping image."}))
                continue

            scene_desc = page_data.get("scene_description", "")
            if style_type == "narrative":
                page_content_text = page_data.get("script_text", "")
                text_key_for_image = "script_text"
            else:
                page_content_text = page_data.get("page_text", "")
                text_key_for_image = "page_text"

            if not page_content_text:
                 print(f"Warning: No '{text_key_for_image}' found in Stage 1 output for page {page_num}.")
                 await websocket.send_text(json.dumps({"status": "warning", "message": f"No text found for page {page_num}. Skipping image."}))
                 continue


            print(f"--- Stage 1 Success for Page {page_num}. ---")
            await websocket.send_text(json.dumps({"status": "progress", "message": f"Page {page_num}: Story content generated."}))


            # --- Stage 2: Generate or Edit Image ---
            await websocket.send_text(json.dumps({"status": "progress", "message": f"Page {page_num}: Generating illustration..."}))
            print(f"--- Running Stage 2: Generating/Editing Image for Page {page_num}... ---")

            mentioned_chars = {
                name: desc for name, desc in characters.items()
                if name.lower() in scene_desc.lower()
            }
            char_details_string = "\n".join([f"- {name}: {desc}" for name, desc in mentioned_chars.items()])
            if not char_details_string:
                 char_details_string = "(No specific characters mentioned in scene description)"

            image_prompt = None
            error2 = None
            img_prompt_template = None
            prompt_template_key = None

            try:
                base_style_key = request.selectedStyle
                prompt_template_key = base_style_key

                if request.modelSelection == 'replicate' and request.referenceImage and request.referenceImageType:
                    specialized_key = f"{base_style_key}_ref_{request.referenceImageType}"
                    if specialized_key in PROMPTS:
                        prompt_template_key = specialized_key
                    else:
                        print(f"Warning: Specialized prompt '{specialized_key}' not found. Using default.")

                elif request.useExperimentalConsistency and page_num > 0: # Use edit template for page 1+ if consistency is on
                     prompt_template_key = f"{request.selectedStyle}_edit"
                     if prompt_template_key not in PROMPTS:
                          print(f"Warning: Edit template '{prompt_template_key}' not found. Falling back to standard.")
                          await websocket.send_text(json.dumps({"status": "warning", "message": f"Edit template missing for page {page_num}, falling back to standard generation."}))
                          prompt_template_key = request.selectedStyle


                img_prompt_template = PROMPTS.get(prompt_template_key, {}).get('prompt_template')

                if not img_prompt_template:
                     raise KeyError(f"Image prompt template '{prompt_template_key}' not found in prompts.json")

                style_description = PROMPTS.get(base_style_key, {}).get('description', 'a standard illustration style')

                image_prompt = img_prompt_template.format(
                    scene_description=scene_desc,
                    character_details_string=char_details_string,
                    page_text=page_content_text if text_key_for_image == "page_text" else "",
                    script_text=page_content_text if text_key_for_image == "script_text" else "",
                    style_description=style_description
                )
            except KeyError as e:
                print(f"Error accessing image prompt template or formatting for page {page_num}: {e}")
                await websocket.send_text(json.dumps({"status": "warning", "message": f"Error formatting image prompt for page {page_num}: {e}. Skipping image."}))
                continue
            except Exception as e:
                 print(f"Error formatting image prompt for page {page_num}: {e}")
                 await websocket.send_text(json.dumps({"status": "warning", "message": f"Error formatting image prompt for page {page_num}: {e}. Skipping image."}))
                 continue


            image_data = None
            error2 = None

            if request.modelSelection == 'replicate':
                print(f"--- Using Replicate Image Generation for Page {page_num} ---")
                image_data, error2 = generate_image_with_replicate(
                    prompt_text=image_prompt,
                    input_image=request.referenceImage,
                    safety_tolerance=request.safetyTolerance
                )
                if error2:
                    print(f"Error during Replicate image generation for page {page_num}: {error2}")
                    await websocket.send_text(json.dumps({"status": "warning", "message": f"Replicate image generation failed for page {page_num}: {error2}. Skipping image."}))
                    image_data = None
            elif request.useExperimentalConsistency and page_num > 0 and previous_page_image_data:
                print(f"--- Using Image Editing for Page {page_num} ---")
                image_data, error2 = edit_image_from_prompt(
                    previous_page_image_data,
                    prompt_text=image_prompt,
                    size="1536x1024",
                    quality="high"
                )
                if error2:
                     print(f"Error during image editing for page {page_num}: {error2}")
                     await websocket.send_text(json.dumps({"status": "warning", "message": f"Image editing failed for page {page_num}: {error2}. Falling back to standard generation."}))
                     print("Falling back to standard generation...")
                     image_data, error2 = generate_image_from_prompt(
                         prompt_text=image_prompt,
                         size="1536x1024",
                         quality="high"
                     )
                     if error2:
                          print(f"Error during fallback standard generation for page {page_num}: {error2}")
                          await websocket.send_text(json.dumps({"status": "warning", "message": f"Standard generation also failed for page {page_num}: {error2}. Skipping image."}))
                          image_data = None

            else:
                print(f"--- Using Standard Image Generation for Page {page_num} ---")
                image_data, error2 = generate_image_from_prompt(
                    prompt_text=image_prompt,
                    size="1536x1024",
                    quality="high"
                )
                if error2:
                     print(f"Error during standard image generation for page {page_num}: {error2}")
                     await websocket.send_text(json.dumps({"status": "warning", "message": f"Image generation failed for page {page_num}: {error2}. Skipping image."}))
                     image_data = None


            # Save Image (if successful)
            if image_data:
                output_filename = os.path.join(book_dir, f"page_{page_num:02d}.png")
                try:
                    with open(output_filename, "wb") as f:
                        f.write(image_data)
                    print(f"--- Stage 2 Success: Page {page_num} image saved successfully as {output_filename} ---")
                    await websocket.send_text(json.dumps({"status": "progress", "message": f"Page {page_num}: Illustration saved."}))

                    if request.useExperimentalConsistency:
                        previous_page_image_data = image_data

                except IOError as e:
                    print(f"Error saving image {output_filename}: {e}")
                    await websocket.send_text(json.dumps({"status": "warning", "message": f"Error saving image for page {page_num}: {e}. Continuing."}))

            # Optional delay
            # time.sleep(1)

        print("\n--- Book Generation Process Completed ---")
        await websocket.send_text(json.dumps({"status": "complete", "message": "Book generation finished!", "output_dir": book_dir}))

    except WebSocketDisconnect:
        print("Frontend disconnected during generation.")
        # Clean up or log as needed
    except json.JSONDecodeError:
        print("Received invalid JSON data over WebSocket.")
        try:
            await websocket.send_text(json.dumps({"status": "error", "message": "Invalid JSON data received."}))
        except Exception:
            pass # Ignore if sending error message fails
    except Exception as e:
        print(f"An unexpected error occurred during WebSocket generation: {e}")
        try:
            await websocket.send_text(json.dumps({"status": "error", "message": f"An unexpected error occurred: {e}"}))
        except Exception:
            pass # Ignore if closing error message fails
    finally:
        # Ensure the WebSocket is closed if not already
        try:
            await websocket.close()
        except Exception:
            pass # Ignore if closing fails


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
