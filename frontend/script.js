var isFirstResponse = true;

var $messages = $('.messages-content'),
    d, h, m,
    id = getOrCreateSessionId();

/* =======================
   INITIAL LOAD
======================= */
$(window).load(function () {
  $messages.mCustomScrollbar();

  setTimeout(function () {
    showLoadingMessage();

    setTimeout(function () {
      firstMessage();
    }, 2000);

  }, 100);
});

/* =======================
   SESSION
======================= */
function generateSessionId() {
  return 'sess_' + Math.random().toString(36).substr(2, 9);
}

function getOrCreateSessionId() {
  try {
    let stored = localStorage.getItem('obelius_session_id');
    if (!stored) {
      stored = generateSessionId();
      localStorage.setItem('obelius_session_id', stored);
    }
    return stored;
  } catch (e) {
    return generateSessionId();
  }
}

/* =======================
   SCROLL
======================= */
function updateScrollbar() {
  $messages
    .mCustomScrollbar("update")
    .mCustomScrollbar('scrollTo', 'bottom', {
      scrollInertia: 10,
      timeout: 0
    });
}

/* =======================
   TIME
======================= */
function setDate() {
  d = new Date();

  let hours = addLeadingZero(d.getHours());
  let minutes = addLeadingZero(d.getMinutes());

  if (m != d.getMinutes()) {
    m = d.getMinutes();

    $('<div class="timestamp">' + hours + ':' + minutes + '</div>')
      .appendTo($('.message:last'));
  }
}

function addLeadingZero(num) {
  return (num < 10 ? '0' : '') + num;
}

/* =======================
   SEND MESSAGE
======================= */
function insertMessage() {
  let msg = $('.message-input').val();

  if ($.trim(msg) == '') return false;

  $('<div class="message message-personal">' + msg + '</div>')
    .appendTo($('.mCSB_container'))
    .addClass('new');

  setDate();
  $('.message-input').val(null);
  updateScrollbar();

  saveToHistory('user', msg, []);
  sendMessageToServer(msg);
}

$('.message-submit').click(function () {
  insertMessage();
});

$(window).on('keydown', function (e) {
  if (e.which == 13) {
    insertMessage();
    return false;
  }
});

/* =======================
   ALERT SYSTEM
======================= */
function showAlert(message) {
  const alertBox = document.getElementById("alert");
  const messageBox = document.getElementById("alert-message");
  const temporary = document.getElementById("content");

  if (!messageBox) return;

  messageBox.textContent = message;
  alertBox.style.display = "block";

  if (temporary) {
    temporary.style.setProperty("--tw-backdrop-blur", "blur(1vh)");
    temporary.style.backdropFilter =
      "var(--tw-backdrop-blur) var(--tw-backdrop-brightness) var(--tw-backdrop-contrast)";
    temporary.style.width = "100%";
    temporary.style.height = "100%";
    temporary.style.zIndex = "1000";
  }

  setTimeout(() => {
    alertBox.classList.remove("hidden");
  }, 10);
}

/* =======================
   SERVER COMMUNICATION  (streaming SSE)
======================= */
function sendMessageToServer(message) {
  showLoadingMessage();

  const conversation = collectVisibleMessages();
  if (conversation.length === 0) {
    console.warn('No conversation data to send.');
    hideLoadingMessage();
    return;
  }

  fetch('/stream', {
    method:  'POST',
    headers: { 'Content-Type': 'application/json' },
    body:    JSON.stringify({ type: 'request', data: conversation, id: id }),
  })
  .then(function (res) {
    if (!res.ok) throw new Error('HTTP ' + res.status);

    // Remove loading dots and create the streaming bubble immediately
    hideLoadingMessage();
    var bubble = createStreamingBubble();

    var reader      = res.body.getReader();
    var decoder     = new TextDecoder();
    var buffer      = '';   // SSE line buffer
    var accumulated = '';   // full plain-text token accumulation

    function read() {
      reader.read().then(function (result) {
        if (result.done) return;

        buffer += decoder.decode(result.value, { stream: true });

        // SSE frames are separated by double newline
        var frames = buffer.split('\n\n');
        buffer = frames.pop(); // last incomplete frame stays in buffer

        frames.forEach(function (frame) {
          var line = frame.trim();
          if (!line.startsWith('data:')) return;

          var jsonStr = line.slice(5).trim();
          var evt;
          try { evt = JSON.parse(jsonStr); } catch (e) { return; }

          if (evt.type === 'token') {
            accumulated += evt.text;
            // Check safety short-circuits on first meaningful token
            var trimmed = accumulated.trim();
            if (trimmed === 'IGNORED') {
              finaliseStreamingBubble(bubble, '', []);
              showAlert('Your activity has been reported!');
              $('.messages-content .message').fadeOut(500);
              setTimeout(function () {
                showLoadingMessage();
                setTimeout(firstMessage, 2000);
              }, 100);
              reader.cancel();
              return;
            }
            if (trimmed === 'PERSONAL') {
              finaliseStreamingBubble(bubble, '', []);
              showAlert('That seems like a personal question.');
              reader.cancel();
              return;
            }
            appendTokenToBubble(bubble, evt.text);
            updateScrollbar();
          }

          else if (evt.type === 'sources') {
            finaliseStreamingBubble(bubble, accumulated, evt.sources || []);
            setDate();
            updateScrollbar();
            saveToHistory('assistant', accumulated, evt.sources || []);
          }

          else if (evt.type === 'error') {
            finaliseStreamingBubble(bubble, evt.text || 'Something went wrong.', []);
            setDate();
            updateScrollbar();
          }

          else if (evt.type === 'done') {
            // sources event already handled finalise; done is just a close signal
          }
        });

        read(); // keep reading
      }).catch(function (err) {
        console.error('[stream] read error', err);
        finaliseStreamingBubble(bubble, accumulated || 'Connection lost.', []);
        updateScrollbar();
      });
    }

    read();
  })
  .catch(function (err) {
    console.error('[stream] fetch error', err);
    hideLoadingMessage();
  });
}

/* =======================
   STREAMING BUBBLE HELPERS
======================= */

// Creates an empty message bubble with a blinking cursor, returns the bubble element
function createStreamingBubble() {
  var bubble = $('<div class="message new streaming"></div>');
  var avatar = '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>';
  var content = $('<div class="message-content"></div>');
  var cursor  = $('<span class="stream-cursor"></span>');

  content.append(cursor);
  bubble.append(avatar).append(content);
  $('.mCSB_container').append(bubble);
  updateScrollbar();
  return bubble;
}

// Appends a raw token string to the bubble's content div (before the cursor)
function appendTokenToBubble(bubble, token) {
  var content = bubble.find('.message-content');
  var cursor  = content.find('.stream-cursor');
  // Insert token as text node before the cursor so cursor stays at end
  cursor.before(document.createTextNode(token));
}

// Called once streaming ends: re-renders accumulated text as markdown,
// removes cursor, appends source chips
function finaliseStreamingBubble(bubble, fullText, sources) {
  var content = bubble.find('.message-content');
  content.find('.stream-cursor').remove();
  bubble.removeClass('streaming');

  if (fullText.trim()) {
    content.html(marked.parse(fullText));
  }

  if (sources && sources.length > 0) {
    var chipsHtml = '<div class="source-chips">';
    sources.forEach(function (src, i) {
      var label = src.title || src.file || src.name || ('Source ' + (i + 1));
      var page  = src.page ? ' · p.' + src.page : '';
      chipsHtml +=
        '<span class="source-chip" title="' + escapeHtmlInline(label + page) + '">' +
        '<i class="fa-solid fa-bookmark"></i> ' + escapeHtmlInline(label) + page +
        '</span>';
    });
    chipsHtml += '</div>';
    bubble.append($(chipsHtml));
  }
}

/* =======================
   RECEIVE MESSAGE  (non-streaming fallback, kept for internal use)
======================= */
function receiveMessage(message, sources) {
  hideLoadingMessage();

  var messageHtml = $('<div class="message new"></div>');
  var avatarHtml  = '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>';
  var contentDiv  = $('<div class="message-content"></div>').html(marked.parse(message));
  messageHtml.append(avatarHtml).append(contentDiv);

  if (sources && sources.length > 0) {
    var chipsHtml = '<div class="source-chips">';
    sources.forEach(function (src, i) {
      var label = src.title || src.file || src.name || ('Source ' + (i + 1));
      var page  = src.page ? ' · p.' + src.page : '';
      chipsHtml +=
        '<span class="source-chip" title="' + escapeHtmlInline(label + page) + '">' +
        '<i class="fa-solid fa-bookmark"></i> ' + escapeHtmlInline(label) + page +
        '</span>';
    });
    chipsHtml += '</div>';
    messageHtml.append($(chipsHtml));
  }

  $('.mCSB_container').append(messageHtml);
  setDate();
  updateScrollbar();
  saveToHistory('assistant', message, sources || []);
}

/* =======================
   DEFAULT / LOADING
======================= */
function firstMessage() {
  let firstMsg = "Hello";

  hideLoadingMessage();

  $('<div class="message new">' +
    '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>' +
    firstMsg +
    '</div>')
    .appendTo($('.mCSB_container'))
    .addClass('new');

  setDate();
  updateScrollbar();
}

function showLoadingMessage() {
  $('<div class="message loading new">' +
    '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>' +
    '<span>Loading...</span>' +
    '</div>')
    .appendTo($('.mCSB_container'));

  updateScrollbar();
}

function hideLoadingMessage() {
  $('.message.loading').remove();
}

/* =======================
   COLLECT CHAT
======================= */
function collectVisibleMessages() {
  const conversation = [];

  $('.messages-content .message')
    .not(':first')
    .each(function () {

      if ($(this).is(':visible')) {

        const messageText = $(this).text().trim();
        const isUserMessage = $(this).hasClass('message-personal');

        if (messageText) {
          conversation.push({
            role: isUserMessage ? "user" : "assistant",
            content: messageText
          });
        }
      }
    });

  return conversation;
}

/* =======================
   LOADER
======================= */
const loadStart = performance.now();

window.addEventListener('load', () => {
  const loader = document.getElementById('loader');
  const content = document.getElementById('content');

  const minimumTime = 500;
  const elapsedTime = performance.now() - loadStart;
  const remainingTime = Math.max(minimumTime - elapsedTime, 0);

  setTimeout(() => {
    loader.classList.add('hidden');

    if (content) {
      content.classList.remove('hidden');
    }

  }, remainingTime);
});

/* =======================
   ALERT CLOSE
======================= */
const alertBox = document.getElementById("alert");
const temporary = document.getElementById("content");
const okBtn = document.getElementById("ok-btn");
const sendbutton = document.getElementById("sendButton");

function hideAlert() {
  alertBox.classList.add("hidden");

  setTimeout(() => {
    if (temporary) {
      temporary.style.removeProperty("--tw-backdrop-blur");
      temporary.style.removeProperty("backdrop-filter");
      temporary.style.removeProperty("width");
      temporary.style.removeProperty("height");
      temporary.style.removeProperty("zIndex");
      temporary.style.zIndex = "2";
    }

    alertBox.style.display = "none";
  }, 300);
}

okBtn.addEventListener("click", hideAlert);

document.addEventListener("click", function (e) {
  const clickedInsideAlert = alertBox.contains(e.target);
  const clickedOkBtn = e.target === okBtn;
  const clickedSendButton = sendbutton?.contains(e.target);

  if (
    (!clickedInsideAlert && !clickedSendButton) ||
    (clickedInsideAlert && clickedOkBtn)
  ) {
    hideAlert();
  }
});

/* =======================
   FILE UPLOAD
======================= */
(function () {

  const uploadToggleBtn = document.getElementById('upload-toggle-btn');
  const uploadPanel     = document.getElementById('upload-panel');
  const dropZone        = document.getElementById('drop-zone');
  const fileInput       = document.getElementById('file-input');
  const browseTrigger   = document.getElementById('browse-trigger');
  const fileList        = document.getElementById('file-list');
  const uploadSubmitBtn = document.getElementById('upload-submit-btn');
  const uploadStatus    = document.getElementById('upload-status');

  // Accepted types
  const ACCEPTED = ['application/pdf',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'text/plain', 'text/csv'];
  const ACCEPTED_EXT = ['.pdf', '.docx', '.txt', '.csv'];

  let stagedFiles = []; // { file, id }

  /* ---- Toggle panel ---- */
  uploadToggleBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    const isOpen = !uploadPanel.classList.contains('collapsed');
    if (isOpen) {
      closePanel();
    } else {
      openPanel();
    }
  });

  function openPanel() {
    uploadPanel.classList.remove('collapsed');
    uploadToggleBtn.classList.add('active');
  }

  function closePanel() {
    uploadPanel.classList.add('collapsed');
    uploadToggleBtn.classList.remove('active');
  }

  /* ---- Browse trigger ---- */
  browseTrigger.addEventListener('click', function (e) {
    e.stopPropagation();
    fileInput.click();
  });

  dropZone.addEventListener('click', function () {
    fileInput.click();
  });

  fileInput.addEventListener('change', function () {
    addFiles(Array.from(fileInput.files));
    fileInput.value = ''; // reset so same file can be re-added after removal
  });

  /* ---- Drag & Drop ---- */
  dropZone.addEventListener('dragenter', function (e) {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.add('drag-over');
  });

  dropZone.addEventListener('dragover', function (e) {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.add('drag-over');
  });

  dropZone.addEventListener('dragleave', function (e) {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.remove('drag-over');
  });

  dropZone.addEventListener('drop', function (e) {
    e.preventDefault();
    e.stopPropagation();
    dropZone.classList.remove('drag-over');
    const dropped = Array.from(e.dataTransfer.files);
    addFiles(dropped);
  });

  /* ---- Full-window drag-over opens panel ---- */
  document.addEventListener('dragenter', function (e) {
    if (e.dataTransfer && e.dataTransfer.types.includes('Files')) {
      openPanel();
    }
  });

  /* ---- Add files to staged list ---- */
  function addFiles(files) {
    files.forEach(function (file) {
      const ext = '.' + file.name.split('.').pop().toLowerCase();
      if (!ACCEPTED_EXT.includes(ext)) return; // silently skip unsupported

      // Deduplicate by name+size
      const alreadyAdded = stagedFiles.some(function (sf) {
        return sf.file.name === file.name && sf.file.size === file.size;
      });
      if (alreadyAdded) return;

      const uid = 'f_' + Math.random().toString(36).substr(2, 8);
      stagedFiles.push({ file: file, id: uid });
      renderFileItem(file, uid);
    });
    refreshSubmitBtn();
  }

  /* ---- Render a single file row ---- */
  function renderFileItem(file, uid) {
    const ext = file.name.split('.').pop().toUpperCase();
    const size = formatFileSize(file.size);
    const safeName = escapeHtml(file.name);

    const item = document.createElement('div');
    item.className = 'file-item';
    item.setAttribute('data-uid', uid);
    item.innerHTML =
      '<span class="file-ext-badge">' + ext + '</span>' +
      '<span class="file-name" title="' + safeName + '">' + safeName + '</span>' +
      '<span class="file-size">' + size + '</span>' +
      '<button class="file-remove" title="Remove"><i class="fa-solid fa-xmark"></i></button>';

    item.querySelector('.file-remove').addEventListener('click', function (e) {
      e.stopPropagation();
      removeFile(uid);
    });

    fileList.appendChild(item);
  }

  /* ---- Remove a file ---- */
  function removeFile(uid) {
    stagedFiles = stagedFiles.filter(function (sf) { return sf.id !== uid; });
    const el = fileList.querySelector('[data-uid="' + uid + '"]');
    if (el) el.remove();
    refreshSubmitBtn();
    setUploadStatus('', '');
  }

  /* ---- Enable / disable upload button ---- */
  function refreshSubmitBtn() {
    uploadSubmitBtn.disabled = stagedFiles.length === 0;
  }

  /* ---- Upload button ---- */
  uploadSubmitBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (stagedFiles.length === 0) return;
    uploadFiles();
  });

  /* ---- Perform upload ---- */
  function uploadFiles() {
    const formData = new FormData();
    stagedFiles.forEach(function (sf) {
      formData.append('files', sf.file);
    });
    formData.append('session_id', id);

    // Mark all items as uploading
    stagedFiles.forEach(function (sf) {
      setItemState(sf.id, 'uploading');
    });
    uploadSubmitBtn.disabled = true;
    setUploadStatus('Uploading...', '');

    fetch('/upload', {
      method: 'POST',
      body: formData
    })
    .then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      return res.json();
    })
    .then(function (data) {
      stagedFiles.forEach(function (sf) {
        setItemState(sf.id, 'success');
      });
      setUploadStatus(
        (data.message || 'Files ready for analysis.'),
        'success'
      );
      // Inject a system message into the chat
      receiveMessage('📎 ' + stagedFiles.length + ' file' +
        (stagedFiles.length > 1 ? 's' : '') + ' uploaded. You can now ask questions about them.');
      // Clear staged after short delay
      setTimeout(function () {
        clearStaged();
      }, 1200);
    })
    .catch(function (err) {
      stagedFiles.forEach(function (sf) {
        setItemState(sf.id, 'error');
      });
      setUploadStatus('Upload failed. Try again.', 'error');
      uploadSubmitBtn.disabled = false;
    });
  }

  /* ---- Clear all staged files ---- */
  function clearStaged() {
    stagedFiles = [];
    fileList.innerHTML = '';
    refreshSubmitBtn();
  }

  /* ---- Set visual state on a file row ---- */
  function setItemState(uid, state) {
    const el = fileList.querySelector('[data-uid="' + uid + '"]');
    if (!el) return;
    el.classList.remove('uploading', 'success', 'error');
    el.classList.add(state);
    const removeBtn = el.querySelector('.file-remove');
    if (removeBtn) removeBtn.style.display = (state === 'uploading') ? 'none' : '';
  }

  /* ---- Status text ---- */
  function setUploadStatus(msg, type) {
    uploadStatus.textContent = msg;
    uploadStatus.className = 'upload-status' + (type ? ' ' + type : '');
  }

  /* ---- Helpers ---- */
  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
  }

  function escapeHtml(str) {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

})();

/* =======================
   AUTO-GROW TEXTAREA
======================= */
(function () {
  const textarea = document.querySelector('.message-input');
  if (!textarea) return;

  // Compute the single-line height once on first interaction
  function autoGrow() {
    // Reset to auto so scrollHeight recalculates correctly
    textarea.style.height = 'auto';
    // Cap at max-height defined in CSS (8vh) — browser clamps via CSS
    textarea.style.height = textarea.scrollHeight + 'px';
    // Hide scrollbar while under max; CSS overflow-y handles the rest
    textarea.style.overflowY = textarea.scrollHeight > textarea.clientHeight ? 'auto' : 'hidden';
  }

  textarea.addEventListener('input', autoGrow);

  // Reset height when message is sent (input value cleared)
  const observer = new MutationObserver(function () {
    if (textarea.value === '') {
      textarea.style.height = '';
      textarea.style.overflowY = 'hidden';
    }
  });

  // Also reset on the existing insertMessage path which clears via .val(null)
  // jQuery .val() doesn't fire 'input', so we patch via a short polling check
  var lastVal = '';
  setInterval(function () {
    if (textarea.value !== lastVal) {
      lastVal = textarea.value;
      if (textarea.value === '') {
        textarea.style.height = '';
        textarea.style.overflowY = 'hidden';
      }
    }
  }, 150);
})();

/* =======================
   INLINE HTML ESCAPE
======================= */
function escapeHtmlInline(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* =======================
   HISTORY
======================= */
(function () {

  var HISTORY_KEY = 'obelius_history_' + (function () {
    try { return localStorage.getItem('obelius_session_id') || 'default'; } catch(e) { return 'default'; }
  })();

  var MAX_HISTORY = 80;

  function loadHistory() {
    try {
      return JSON.parse(localStorage.getItem(HISTORY_KEY)) || [];
    } catch (e) { return []; }
  }

  function saveHistory(items) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(items)); } catch(e) {}
  }

  /* Public — called by receiveMessage and insertMessage */
  window.saveToHistory = function (role, text, sources) {
    var items = loadHistory();
    items.push({ role: role, text: text, sources: sources || [], ts: Date.now() });
    if (items.length > MAX_HISTORY) items = items.slice(-MAX_HISTORY);
    saveHistory(items);
    renderHistoryList();
  };

  function renderHistoryList() {
    var list  = document.getElementById('history-list');
    var items = loadHistory();
    if (!list) return;

    if (items.length === 0) {
      list.innerHTML = '<div class="history-empty">No saved messages yet.</div>';
      return;
    }

    list.innerHTML = '';
    var reversed = items.slice().reverse();
    reversed.forEach(function (item) {
      var row = document.createElement('div');
      row.className = 'history-item history-item--' + item.role;

      var time = new Date(item.ts);
      var hh   = (time.getHours()   < 10 ? '0' : '') + time.getHours();
      var mm   = (time.getMinutes() < 10 ? '0' : '') + time.getMinutes();
      var preview = item.text.replace(/\n/g, ' ').substring(0, 60) + (item.text.length > 60 ? '…' : '');

      var sourceBadge = '';
      if (item.sources && item.sources.length > 0) {
        sourceBadge = '<span class="history-source-count"><i class="fa-solid fa-bookmark"></i> ' + item.sources.length + '</span>';
      }

      row.innerHTML =
        '<span class="history-role">' + (item.role === 'user' ? 'You' : 'AI') + '</span>' +
        '<span class="history-preview">' + escapeHtmlInline(preview) + '</span>' +
        sourceBadge +
        '<span class="history-time">' + hh + ':' + mm + '</span>';

      list.appendChild(row);
    });
  }

  var toggleBtn = document.getElementById('history-toggle-btn');
  var drawer    = document.getElementById('history-drawer');
  var clearBtn  = document.getElementById('history-clear-btn');

  if (toggleBtn) {
    toggleBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      var isOpen = !drawer.classList.contains('collapsed');
      if (isOpen) {
        drawer.classList.add('collapsed');
        toggleBtn.classList.remove('active');
      } else {
        renderHistoryList();
        drawer.classList.remove('collapsed');
        toggleBtn.classList.add('active');
      }
    });
  }

  if (clearBtn) {
    clearBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      saveHistory([]);
      renderHistoryList();
    });
  }

  renderHistoryList();

})();