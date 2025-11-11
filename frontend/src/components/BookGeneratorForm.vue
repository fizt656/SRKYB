<script setup lang="ts">
import { ref, onMounted } from 'vue';

// Reactive variables for form inputs
const bookTitle = ref('');
const selectedStyle = ref(''); // Will store the key of the selected style
const numberOfPages = ref(10); // Default to 10 pages
const quickMode = ref(true); // Default to Quick Mode
const storyOutline = ref(''); // For both modes
const useExperimentalConsistency = ref(false);
const characters = ref<{ name: string; description: string }[]>([
  { name: '', description: '' },
]); // For Full Mode
const modelSelection = ref('openai'); // New: 'openai' or 'replicate'
const referenceImageFile = ref<File | null>(null); // New: For the uploaded image file
const referenceImageType = ref('style'); // New: 'style', 'character', or 'setting'
const safetyTolerance = ref(6); // New: For Replicate safety tolerance

// Reactive variables for generation status
const isGenerating = ref(false);
const generationStatusLog = ref<string[]>([]);
const generationError = ref('');
const generationProgress = ref(0);

// Styles will be fetched from the backend
const availableStyles = ref<{ key: string; desc: string }[]>([]);

// Fetch styles from the backend when the component is mounted
onMounted(async () => {
  try {
    const response = await fetch('http://localhost:8000/api/styles');
    if (!response.ok) {
      throw new Error('Failed to fetch styles');
    }
    availableStyles.value = await response.json();
    // Set a default style if available
    if (availableStyles.value.length > 0) {
      selectedStyle.value = availableStyles.value[0].key;
    }
  } catch (error) {
    console.error('Error fetching styles:', error);
    generationError.value = 'Could not load illustration styles from the backend.';
  }
});

// Function to handle file upload
const handleFileUpload = (event: Event) => {
  const target = event.target as HTMLInputElement;
  if (target.files && target.files.length > 0) {
    referenceImageFile.value = target.files[0];
  } else {
    referenceImageFile.value = null;
  }
};

// Function to add a new character
const addCharacter = () => {
  characters.value.push({ name: '', description: '' });
};

// Function to remove a character
const removeCharacter = (index: number) => {
  characters.value.splice(index, 1);
};

// Function to handle form submission
const generateBook = () => {
  const reader = new FileReader();

  const sendData = (referenceImageData: string | null) => {
    // Convert characters array to JSON string for the backend
    const characterDescriptionsJSON = quickMode.value
      ? null
      : JSON.stringify(
          Object.fromEntries(
            characters.value.map((char) => [char.name, char.description])
          )
        );

    const formData = {
      bookTitle: bookTitle.value,
      selectedStyle: selectedStyle.value,
      numberOfPages: numberOfPages.value,
      quickMode: quickMode.value,
      characterDescriptions: characterDescriptionsJSON,
      storyOutline: storyOutline.value,
      useExperimentalConsistency: useExperimentalConsistency.value,
      modelSelection: modelSelection.value,
      referenceImage: referenceImageData,
      referenceImageType: referenceImageType.value,
      safetyTolerance: safetyTolerance.value,
    };

    // Establish WebSocket connection
    const websocket = new WebSocket('ws://localhost:8000/ws/generate-progress');

    websocket.onopen = () => {
      console.log('WebSocket connection established.');
      isGenerating.value = true;
      generationStatusLog.value = ['Connecting...'];
      generationError.value = '';
      generationProgress.value = 0;
      // Send form data over WebSocket
      websocket.send(JSON.stringify(formData));
      console.log('Form data sent over WebSocket.');
      generationStatusLog.value = [...generationStatusLog.value, 'Generation request sent...'];
    };

    websocket.onmessage = (event) => {
      console.log('Message from backend:', event.data);
      try {
        const message = JSON.parse(event.data);
        const statusMessage = message.message || 'Processing...';

        if (message.status === 'progress' || message.status === 'warning' || message.status === 'complete') {
          generationStatusLog.value = [...generationStatusLog.value, statusMessage];
          if (message.percent) {
            generationProgress.value = message.percent;
          }
        } else if (message.status === 'error') {
          generationError.value = statusMessage;
          generationStatusLog.value = [...generationStatusLog.value, `Error: ${statusMessage}`];
          isGenerating.value = false; // Stop on error
          websocket.close(); // Close connection on error
        }
      } catch (e) {
        console.error('Failed to parse message from backend:', e);
        const errorMessage = 'Received unreadable message from backend.';
        generationStatusLog.value = [...generationStatusLog.value, errorMessage];
        generationError.value = errorMessage;
      }
    };

    websocket.onerror = (error) => {
      console.error('WebSocket error:', error);
      const errorMessage = 'WebSocket connection error. Check that the backend is running.';
      generationError.value = errorMessage;
      generationStatusLog.value = [...generationStatusLog.value, errorMessage];
      isGenerating.value = false;
    };

    websocket.onclose = (event) => {
      console.log('WebSocket connection closed:', event.code, event.reason);
      isGenerating.value = false;
      if (event.wasClean && !generationError.value) {
        // If closed cleanly and no error was set, check the last message
        const lastStatus = generationStatusLog.value[generationStatusLog.value.length - 1];
        if (!lastStatus || !lastStatus.toLowerCase().includes('finished')) {
          generationStatusLog.value = [...generationStatusLog.value, 'Generation complete.'];
        }
        console.log('Book generation finished.');
      } else if (!generationError.value) {
        // If closed uncleanly but no specific error message was set
        const errorMessage = `WebSocket closed unexpectedly (Code: ${event.code})`;
        generationError.value = errorMessage;
        generationStatusLog.value = [...generationStatusLog.value, errorMessage];
        console.error('Book generation finished with WebSocket error.');
      }
      // If generationError was already set, keep that message.
    };
  };

  if (modelSelection.value === 'replicate' && referenceImageFile.value) {
    reader.onload = (e) => {
      const imageDataUrl = e.target?.result as string;
      sendData(imageDataUrl);
    };
    reader.readAsDataURL(referenceImageFile.value);
  } else {
    sendData(null);
  }
};
</script>

<template>
  <div class="book-generator-form">
    <div class="form-group">
      <label>Image Generation Model:</label>
      <div class="radio-group">
        <label>
          <input type="radio" value="openai" v-model="modelSelection" /> OpenAI GPT Image-1
        </label>
        <label>
          <input type="radio" value="replicate" v-model="modelSelection" /> Replicate FLUX Kontext
        </label>
      </div>
    </div>

    <div class="form-group" v-if="modelSelection === 'replicate'">
      <label for="referenceImage">Reference Image (Optional):</label>
      <input type="file" id="referenceImage" @change="handleFileUpload" accept="image/*" />
    </div>

    <div class="form-group" v-if="modelSelection === 'replicate' && referenceImageFile">
      <label>Use Reference Image For:</label>
      <div class="radio-group">
        <label>
          <input type="radio" value="style" v-model="referenceImageType" /> Style
        </label>
        <label>
          <input type="radio" value="character" v-model="referenceImageType" /> Character(s)
        </label>
        <label>
          <input type="radio" value="setting" v-model="referenceImageType" /> Setting/Background
        </label>
      </div>
    </div>

    <div class="form-group" v-if="modelSelection === 'replicate'">
      <label for="safetyTolerance">Safety Tolerance (0-6):</label>
      <input type="range" id="safetyTolerance" min="0" max="6" v-model.number="safetyTolerance" />
      <span>{{ safetyTolerance }}</span>
    </div>

    <div class="form-group">
      <label for="bookTitle">Book Title:</label>
      <input type="text" id="bookTitle" v-model="bookTitle" required />
    </div>

    <div class="form-group">
      <label for="style">Illustration Style:</label>
      <select id="style" v-model="selectedStyle" required>
        <option value="" disabled>Select a style</option>
        <option v-for="style in availableStyles" :key="style.key" :value="style.key">
          {{ style.desc }}
        </option>
      </select>
    </div>

    <div class="form-group">
      <label for="numberOfPages">Number of Pages:</label>
      <input type="number" id="numberOfPages" v-model.number="numberOfPages" min="1" required />
    </div>

    <div class="form-group">
      <label>Generation Mode:</label>
      <div class="radio-group">
        <label>
          <input type="radio" :value="true" v-model="quickMode" /> Quick Mode
        </label>
        <label>
          <input type="radio" :value="false" v-model="quickMode" /> Full Mode
        </label>
      </div>
    </div>

    <div class="form-group" v-if="!quickMode">
      <label>Character Descriptions:</label>
      <div v-for="(character, index) in characters" :key="index" class="character-entry">
        <input type="text" placeholder="Character Name" v-model="character.name" class="char-name-input" />
        <input type="text" placeholder="Character Description" v-model="character.description" class="char-desc-input" />
        <button @click="removeCharacter(index)" class="remove-char-button" type="button">-</button>
      </div>
      <button @click="addCharacter" class="add-char-button" type="button">Add Character</button>
    </div>

    <div class="form-group">
      <label for="storyOutline">Story Outline/Concept:</label>
      <textarea id="storyOutline" v-model="storyOutline" rows="6" required></textarea>
    </div>

    <div class="form-group checkbox-group">
      <input type="checkbox" id="experimentalConsistency" v-model="useExperimentalConsistency" />
      <label for="experimentalConsistency">Enable Experimental Consistency Mode</label>
    </div>

    <button @click="generateBook" class="generate-button" :disabled="isGenerating">
      {{ isGenerating ? 'Generating...' : 'Generate Book' }}
    </button>

    <div v-if="generationStatusLog.length > 0 || generationError" class="status-area">
      <div class="status-log">
        <p v-for="(status, index) in generationStatusLog" :key="index" :class="{ 'error-text': status.toLowerCase().includes('error') }">
          {{ status }}
        </p>
      </div>
      <p v-if="generationError" class="error-text">Error: {{ generationError }}</p>
      <div class="progress-bar-container" v-if="isGenerating && generationProgress > 0">
        <div class="progress-bar" :style="{ width: generationProgress + '%' }"></div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.book-generator-form {
  max-width: 600px;
  margin: 0 auto;
  padding: 2rem;
  background-color: var(--color-background-soft); /* Use a soft background */
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.form-title {
  color: var(--color-heading);
  text-align: center;
  margin-bottom: 1.5rem;
}

.form-group {
  margin-bottom: 1.5rem;
}

.form-group label {
  display: block;
  margin-bottom: 0.5rem;
  color: var(--color-text);
  font-weight: bold;
}

.form-group input[type="text"],
.form-group input[type="number"],
.form-group select,
.form-group textarea {
  width: 100%;
  padding: 0.8rem;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background-color: var(--color-background);
  color: var(--color-text);
  box-sizing: border-box; /* Include padding and border in element's total width and height */
}

.form-group textarea {
  resize: vertical; /* Allow vertical resizing */
}

.character-entry {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.5rem;
  align-items: center;
}

.char-name-input {
  flex: 1;
}

.char-desc-input {
  flex: 3;
}

.remove-char-button,
.add-char-button {
  padding: 0.5rem 0.8rem;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  font-weight: bold;
}

.remove-char-button {
  background-color: #dc3545;
  color: white;
}

.add-char-button {
  background-color: #28a745;
  color: white;
  margin-top: 0.5rem;
}

.radio-group label {
  display: inline-block;
  margin-right: 1rem;
  font-weight: normal;
}

.checkbox-group {
  display: flex;
  align-items: center;
}

.checkbox-group input[type="checkbox"] {
  margin-right: 0.5rem;
}

.generate-button {
  display: block;
  width: 100%;
  padding: 1rem;
  background-color: #00A9E0; /* Bright Blue from logo */
  color: white;
  border: none;
  border-radius: 4px;
  font-size: 1.1rem;
  font-weight: bold;
  cursor: pointer;
  transition: background-color 0.3s ease, box-shadow 0.3s ease;
}

.generate-button:hover {
  background-color: #00C2B8; /* Turquoise from logo */
  box-shadow: 0 4px 12px rgba(0, 194, 184, 0.4); /* Subtle glow effect */
}

.generate-button:disabled {
  background-color: #ccc;
  cursor: not-allowed;
  box-shadow: none;
}

.status-area {
  margin-top: 1.5rem;
  padding: 1rem;
  background-color: var(--color-background);
  border: 1px solid var(--color-border);
  border-radius: 4px;
}

.status-log {
  max-height: 200px;
  overflow-y: auto;
  text-align: left;
  padding: 0.5rem;
  border-radius: 4px;
  background-color: var(--color-background-mute);
}

.status-log p {
  margin: 0 0 0.5rem;
  padding: 0;
}

.status-log p:last-child {
  margin-bottom: 0;
}

.error-text {
  color: #dc3545; /* Bootstrap danger color */
  font-weight: bold;
}

.progress-bar-container {
  width: 100%;
  background-color: var(--color-border);
  border-radius: 4px;
  margin-top: 1rem;
}

.progress-bar {
  height: 10px;
  background-color: #00A9E0; /* Bright Blue from logo */
  border-radius: 4px;
  transition: width 0.3s ease-in-out;
}

/* Basic dark mode adjustments (inherits from global styles) */
/* Add specific overrides here if needed */
</style>
