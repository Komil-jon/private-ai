/* ============================================================
   script.js  —  Obelius Private AI
   ============================================================ */

/* =======================
   STATE
======================= */
var isFirstResponse = true;

var currentUser  = null;   // { user_id, username, email, full_name } or null
var currentToken = null;   // JWT string or null
var activeConvId = null;   // MongoDB _id string (logged-in only)

// Guest session id — still used as the document-store key for uploads
var id = getOrCreateSessionId();

var $messages = $('.messages-content');
var d, h, m;

/* =======================
   INITIAL LOAD
======================= */
$(window).on('load', function () {
  $messages.mCustomScrollbar();

  setTimeout(function () {
    showLoadingMessage();
    checkAuth().then(function () {
      setTimeout(firstMessage, 2000);
    });
  }, 100);
});

/* =======================
   AUTH  — check on load
======================= */
var AUTH_BASE = 'https://auth.eternal.uz';

async function checkAuth() {
  try {
    var token = localStorage.getItem('eternal_token');
    if (!token) { renderAuthState(null); return; }

    var resp = await fetch(AUTH_BASE + '/api/verify', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ token: token }),
    });
    if (!resp.ok) { renderAuthState(null); return; }

    var data = await resp.json();
    if (!data.valid) {
      localStorage.removeItem('eternal_token');
      renderAuthState(null);
      return;
    }

    currentToken = token;
    currentUser  = data.user;
    renderAuthState(currentUser);
    await loadConversations();

    // Restore last active conversation
    var lastConv = localStorage.getItem('obelius_last_conv');
    if (lastConv) {
      await openConversation(lastConv, false);
    }

  } catch (e) {
    console.warn('[auth] check failed:', e);
    renderAuthState(null);
  }
}

function renderAuthState(user) {
  var guestEl    = document.getElementById('drawer-guest');
  var userEl     = document.getElementById('drawer-user');
  var usernameEl = document.getElementById('drawer-username');
  var convList   = document.getElementById('conv-list');

  if (user) {
    if (guestEl)    guestEl.classList.add('hidden-auth');
    if (userEl)     userEl.classList.remove('hidden-auth');
    if (usernameEl) usernameEl.textContent = user.full_name || user.username;
    if (convList)   convList.innerHTML = '';
  } else {
    if (guestEl)   guestEl.classList.remove('hidden-auth');
    if (userEl)    userEl.classList.add('hidden-auth');
    if (convList)  convList.innerHTML =
      '<div class="conv-empty">Sign in to see your conversations.</div>';
  }
}

/* =======================
   AUTH  — sign in / out
======================= */
document.getElementById('drawer-login-btn').addEventListener('click', function (e) {
  e.preventDefault();
  var redirect = encodeURIComponent(window.location.href);
  window.location.href = AUTH_BASE + '?redirect=' + redirect;
});

document.getElementById('drawer-logout-btn').addEventListener('click', function () {
  localStorage.removeItem('eternal_token');
  localStorage.removeItem('eternal_user');
  localStorage.removeItem('obelius_last_conv');
  currentToken = null;
  currentUser  = null;
  activeConvId = null;
  renderAuthState(null);
  clearChat();
  firstMessage();
});

/* =======================
   SESSION (guest fallback)
======================= */
function generateSessionId() {
  return 'sess_' + Math.random().toString(36).substr(2, 9);
}
function getOrCreateSessionId() {
  try {
    var stored = localStorage.getItem('obelius_session_id');
    if (!stored) {
      stored = generateSessionId();
      localStorage.setItem('obelius_session_id', stored);
    }
    return stored;
  } catch (e) { return generateSessionId(); }
}

/* =======================
   CONVERSATIONS DRAWER
======================= */
async function loadConversations() {
  if (!currentToken) return;
  try {
    var resp = await fetch('/api/conversations', {
      headers: { 'Authorization': 'Bearer ' + currentToken }
    });
    if (!resp.ok) return;
    var convs = await resp.json();
    renderConversationList(convs);
  } catch (e) {
    console.warn('[convs] load failed:', e);
  }
}

function renderConversationList(convs) {
  var list = document.getElementById('conv-list');
  if (!list) return;

  if (!convs || convs.length === 0) {
    list.innerHTML = '<div class="conv-empty">No conversations yet.<br>Start one below.</div>';
    return;
  }

  list.innerHTML = '';
  convs.forEach(function (conv) {
    var item = document.createElement('div');
    item.className = 'conv-item' + (conv.id === activeConvId ? ' active' : '');
    item.setAttribute('data-id', conv.id);

    var date    = new Date(conv.updated_at);
    var timeStr = date.toLocaleDateString([], { month: 'short', day: 'numeric' });

    item.innerHTML =
      '<i class="fa-regular fa-message conv-item-icon"></i>' +
      '<span class="conv-item-title">' + escapeHtmlInline(conv.title) + '</span>' +
      '<span class="conv-item-date">' + timeStr + '</span>' +
      '<button class="conv-item-delete" title="Delete"><i class="fa-solid fa-xmark"></i></button>';

    item.addEventListener('click', function (e) {
      if (e.target.closest('.conv-item-delete')) return;
      openConversation(conv.id, true);
      // Close the drawer after selection
      closeDrawer();
    });

    item.querySelector('.conv-item-delete').addEventListener('click', function (e) {
      e.stopPropagation();
      deleteConversation(conv.id);
    });

    list.appendChild(item);
  });
}

async function openConversation(convId, clearScreen) {
  if (!currentToken) return;
  activeConvId = convId;
  localStorage.setItem('obelius_last_conv', convId);

  document.querySelectorAll('.conv-item').forEach(function (el) {
    el.classList.toggle('active', el.getAttribute('data-id') === convId);
  });

  if (clearScreen) clearChat();

  try {
    var resp = await fetch('/api/conversations/' + convId + '/messages', {
      headers: { 'Authorization': 'Bearer ' + currentToken }
    });
    if (!resp.ok) return;
    var msgs = await resp.json();

    msgs.forEach(function (msg) {
      if (msg.role === 'user') {
        $('<div class="message message-personal">' + escapeHtmlInline(msg.content) + '</div>')
          .appendTo($('.mCSB_container'));
      } else {
        var bubble  = $('<div class="message new"></div>');
        var avatar  = '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>';
        var content = $('<div class="message-content"></div>').html(marked.parse(msg.content));
        bubble.append(avatar).append(content);
        if (msg.sources && msg.sources.length > 0) {
          bubble.append(buildSourceChips(msg.sources));
        }
        $('.mCSB_container').append(bubble);
      }
    });

    setDate();
    updateScrollbar();

    if (msgs.length === 0) firstMessage();

  } catch (e) {
    console.warn('[conv] load messages failed:', e);
  }
}

async function newConversation() {
  if (!currentToken) return;
  try {
    var resp = await fetch('/api/conversations', {
      method:  'POST',
      headers: {
        'Content-Type':  'application/json',
        'Authorization': 'Bearer ' + currentToken,
      },
      body: JSON.stringify({}),
    });
    if (!resp.ok) return;
    var conv = await resp.json();
    activeConvId = conv.id;
    localStorage.setItem('obelius_last_conv', conv.id);
    clearChat();
    firstMessage();
    await loadConversations();
    closeDrawer();
  } catch (e) {
    console.warn('[conv] create failed:', e);
  }
}

async function deleteConversation(convId) {
  if (!currentToken) return;
  try {
    await fetch('/api/conversations/' + convId, {
      method:  'DELETE',
      headers: { 'Authorization': 'Bearer ' + currentToken },
    });
    if (activeConvId === convId) {
      activeConvId = null;
      localStorage.removeItem('obelius_last_conv');
      clearChat();
      firstMessage();
    }
    await loadConversations();
  } catch (e) {
    console.warn('[conv] delete failed:', e);
  }
}

async function deleteAllConversations() {
  if (!currentToken) return;
  try {
    await fetch('/api/conversations/all', {
      method:  'DELETE',
      headers: { 'Authorization': 'Bearer ' + currentToken },
    });
    activeConvId = null;
    localStorage.removeItem('obelius_last_conv');
    clearChat();
    firstMessage();
    await loadConversations();
  } catch (e) {
    console.warn('[conv] delete all failed:', e);
  }
}

function clearChat() {
  $('.mCSB_container').empty();
  isFirstResponse = true;
}

/* =======================
   DRAWER TOGGLE
   (history-toggle-btn now controls the unified conversations drawer)
======================= */
function closeDrawer() {
  var drawer    = document.getElementById('history-drawer');
  var toggleBtn = document.getElementById('history-toggle-btn');
  if (drawer)    drawer.classList.add('collapsed');
  if (toggleBtn) toggleBtn.classList.remove('active');
}

(function () {
  var toggleBtn = document.getElementById('history-toggle-btn');
  var drawer    = document.getElementById('history-drawer');

  if (!toggleBtn || !drawer) return;

  toggleBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    var isOpen = !drawer.classList.contains('collapsed');
    if (isOpen) {
      closeDrawer();
    } else {
      // Refresh list when opening
      if (currentUser) loadConversations();
      drawer.classList.remove('collapsed');
      toggleBtn.classList.add('active');
    }
  });

  // Close drawer when clicking outside the chat card
  document.addEventListener('click', function (e) {
    var chat = document.querySelector('.chat');
    if (chat && !chat.contains(e.target)) {
      closeDrawer();
    }
  });
})();

/* New chat button */
document.getElementById('new-chat-btn').addEventListener('click', function (e) {
  e.stopPropagation();
  if (currentUser) {
    newConversation();
  } else {
    clearChat();
    firstMessage();
    closeDrawer();
  }
});

/* Delete all button */
document.getElementById('delete-all-btn').addEventListener('click', function (e) {
  e.stopPropagation();
  if (currentUser) {
    deleteAllConversations();
  } else {
    clearChat();
    firstMessage();
  }
});

/* =======================
   SCROLL
======================= */
function updateScrollbar() {
  $messages
    .mCustomScrollbar('update')
    .mCustomScrollbar('scrollTo', 'bottom', { scrollInertia: 10, timeout: 0 });
}

/* =======================
   TIME
======================= */
function setDate() {
  d = new Date();
  var hours   = addLeadingZero(d.getHours());
  var minutes = addLeadingZero(d.getMinutes());
  if (m != d.getMinutes()) {
    m = d.getMinutes();
    $('<div class="timestamp">' + hours + ':' + minutes + '</div>')
      .appendTo($('.message:last'));
  }
}
function addLeadingZero(num) { return (num < 10 ? '0' : '') + num; }

/* =======================
   SEND MESSAGE
======================= */
function insertMessage() {
  var msg = $('.message-input').val();
  if ($.trim(msg) === '') return false;

  $('<div class="message message-personal">' + escapeHtmlInline(msg) + '</div>')
    .appendTo($('.mCSB_container'))
    .addClass('new');

  setDate();
  $('.message-input').val(null);
  updateScrollbar();

  sendMessageToServer(msg);
}

$('.message-submit').on('click', function () { insertMessage(); });

$(window).on('keydown', function (e) {
  if (e.which === 13 && !e.shiftKey) { insertMessage(); return false; }
});

/* =======================
   SERVER COMMUNICATION  (streaming SSE)
======================= */
function sendMessageToServer(message) {
  showLoadingMessage();

  var conversation = collectVisibleMessages();
  if (conversation.length === 0) {
    hideLoadingMessage();
    return;
  }

  var headers = { 'Content-Type': 'application/json' };
  if (currentToken) headers['Authorization'] = 'Bearer ' + currentToken;

  var body = {
    type:    'request',
    data:    conversation,
    id:      id,
    message: message,
  };
  if (activeConvId) body.conv_id = activeConvId;

  fetch('/stream', { method: 'POST', headers: headers, body: JSON.stringify(body) })
  .then(function (res) {
    if (!res.ok) throw new Error('HTTP ' + res.status);
    hideLoadingMessage();
    var bubble      = createStreamingBubble();
    var reader      = res.body.getReader();
    var decoder     = new TextDecoder();
    var buffer      = '';
    var accumulated = '';

    function read() {
      reader.read().then(function (result) {
        if (result.done) return;

        buffer += decoder.decode(result.value, { stream: true });
        var frames = buffer.split('\n\n');
        buffer = frames.pop();

        frames.forEach(function (frame) {
          var line = frame.trim();
          if (!line.startsWith('data:')) return;
          var jsonStr = line.slice(5).trim();
          var evt;
          try { evt = JSON.parse(jsonStr); } catch (e) { return; }

          if (evt.type === 'token') {
            accumulated += evt.text;
            var trimmed = accumulated.trim();
            if (trimmed === 'IGNORED') {
              finaliseStreamingBubble(bubble, '', []);
              showAlert('Your activity has been reported!');
              $('.messages-content .message').fadeOut(500);
              setTimeout(function () { showLoadingMessage(); setTimeout(firstMessage, 2000); }, 100);
              reader.cancel(); return;
            }
            if (trimmed === 'PERSONAL') {
              finaliseStreamingBubble(bubble, '', []);
              showAlert('That seems like a personal question.');
              reader.cancel(); return;
            }
            appendTokenToBubble(bubble, evt.text);
            updateScrollbar();
          }

          else if (evt.type === 'sources') {
            finaliseStreamingBubble(bubble, accumulated, evt.sources || []);
            setDate();
            updateScrollbar();
          }

          else if (evt.type === 'done') {
            if (evt.conv_id && evt.conv_id !== id) {
              activeConvId = evt.conv_id;
              localStorage.setItem('obelius_last_conv', activeConvId);
              if (currentUser) setTimeout(loadConversations, 500);
            }
          }

          else if (evt.type === 'error') {
            finaliseStreamingBubble(bubble, evt.text || 'Something went wrong.', []);
            setDate(); updateScrollbar();
          }
        });

        read();
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
function createStreamingBubble() {
  var bubble  = $('<div class="message new streaming"></div>');
  var avatar  = '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>';
  var content = $('<div class="message-content"></div>');
  var cursor  = $('<span class="stream-cursor"></span>');
  content.append(cursor);
  bubble.append(avatar).append(content);
  $('.mCSB_container').append(bubble);
  updateScrollbar();
  return bubble;
}

function appendTokenToBubble(bubble, token) {
  var content = bubble.find('.message-content');
  var cursor  = content.find('.stream-cursor');
  cursor.before(document.createTextNode(token));
}

function finaliseStreamingBubble(bubble, fullText, sources) {
  var content = bubble.find('.message-content');
  content.find('.stream-cursor').remove();
  bubble.removeClass('streaming');
  if (fullText.trim()) { content.html(marked.parse(fullText)); }
  if (sources && sources.length > 0) { bubble.append(buildSourceChips(sources)); }
}

function buildSourceChips(sources) {
  var html = '<div class="source-chips">';
  sources.forEach(function (src, i) {
    var label = src.title || src.file || src.name || ('Source ' + (i + 1));
    var page  = src.page ? ' · p.' + src.page : '';
    html +=
      '<span class="source-chip" title="' + escapeHtmlInline(label + page) + '">' +
      '<i class="fa-solid fa-bookmark"></i> ' + escapeHtmlInline(label) + page +
      '</span>';
  });
  html += '</div>';
  return $(html);
}

/* =======================
   RECEIVE MESSAGE (system messages, file upload confirmations)
======================= */
function receiveMessage(message, sources) {
  hideLoadingMessage();
  var messageHtml = $('<div class="message new"></div>');
  var avatarHtml  = '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>';
  var contentDiv  = $('<div class="message-content"></div>').html(marked.parse(message));
  messageHtml.append(avatarHtml).append(contentDiv);
  if (sources && sources.length > 0) { messageHtml.append(buildSourceChips(sources)); }
  $('.mCSB_container').append(messageHtml);
  setDate(); updateScrollbar();
}

/* =======================
   FIRST / LOADING MESSAGES
======================= */
function firstMessage() {
  hideLoadingMessage();
  var greeting = currentUser
    ? 'Hello, ' + (currentUser.full_name || currentUser.username) + '!'
    : 'Hello';

  $('<div class="message new">' +
    '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>' +
    escapeHtmlInline(greeting) +
    '</div>')
    .appendTo($('.mCSB_container')).addClass('new');

  setDate(); updateScrollbar();
}

function showLoadingMessage() {
  $('<div class="message loading new">' +
    '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>' +
    '<span>Loading...</span></div>')
    .appendTo($('.mCSB_container'));
  updateScrollbar();
}

function hideLoadingMessage() { $('.message.loading').remove(); }

/* =======================
   COLLECT CHAT (guest / fallback context for AI)
======================= */
function collectVisibleMessages() {
  var conversation = [];
  $('.messages-content .message').not(':first').each(function () {
    if ($(this).is(':visible')) {
      var text = $(this).find('.message-content').text() || $(this).text();
      text = text.trim();
      var isUser = $(this).hasClass('message-personal');
      if (text) conversation.push({ role: isUser ? 'user' : 'assistant', content: text });
    }
  });
  return conversation;
}

/* =======================
   LOADER
======================= */
var loadStart = performance.now();
window.addEventListener('load', function () {
  var loader    = document.getElementById('loader');
  var content   = document.getElementById('content');
  var elapsed   = performance.now() - loadStart;
  var remaining = Math.max(500 - elapsed, 0);
  setTimeout(function () {
    loader.classList.add('hidden');
    if (content) content.classList.remove('hidden');
  }, remaining);
});

/* =======================
   ALERT SYSTEM
======================= */
function showAlert(message) {
  var alertBox   = document.getElementById('alert');
  var messageBox = document.getElementById('alert-message');
  if (!messageBox) return;
  messageBox.textContent = message;
  alertBox.style.display = 'block';
  setTimeout(function () { alertBox.classList.remove('hidden'); }, 10);
}

function hideAlert() {
  var alertBox  = document.getElementById('alert');
  var temporary = document.getElementById('content');
  alertBox.classList.add('hidden');
  setTimeout(function () {
    if (temporary) {
      temporary.style.removeProperty('--tw-backdrop-blur');
      temporary.style.removeProperty('backdrop-filter');
      temporary.style.zIndex = '2';
    }
    alertBox.style.display = 'none';
  }, 300);
}

var okBtn = document.getElementById('ok-btn');
if (okBtn) okBtn.addEventListener('click', hideAlert);

document.addEventListener('click', function (e) {
  var alertBox = document.getElementById('alert');
  if (!alertBox) return;
  if (!alertBox.classList.contains('hidden') && !alertBox.contains(e.target)) {
    hideAlert();
  }
});

/* =======================
   FILE UPLOAD
======================= */
(function () {
  var uploadToggleBtn = document.getElementById('upload-toggle-btn');
  var uploadPanel     = document.getElementById('upload-panel');
  var dropZone        = document.getElementById('drop-zone');
  var fileInput       = document.getElementById('file-input');
  var browseTrigger   = document.getElementById('browse-trigger');
  var fileList        = document.getElementById('file-list');
  var uploadSubmitBtn = document.getElementById('upload-submit-btn');
  var uploadStatus    = document.getElementById('upload-status');

  var ACCEPTED_EXT = ['.pdf', '.docx', '.txt', '.csv'];
  var stagedFiles  = [];

  uploadToggleBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    uploadPanel.classList.toggle('collapsed');
    uploadToggleBtn.classList.toggle('active', !uploadPanel.classList.contains('collapsed'));
  });

  browseTrigger.addEventListener('click', function (e) { e.stopPropagation(); fileInput.click(); });
  dropZone.addEventListener('click', function () { fileInput.click(); });

  fileInput.addEventListener('change', function () {
    addFiles(Array.from(fileInput.files));
    fileInput.value = '';
  });

  ['dragenter','dragover'].forEach(function (ev) {
    dropZone.addEventListener(ev, function (e) {
      e.preventDefault(); e.stopPropagation();
      dropZone.classList.add('drag-over');
    });
  });
  dropZone.addEventListener('dragleave', function (e) {
    e.preventDefault(); e.stopPropagation();
    dropZone.classList.remove('drag-over');
  });
  dropZone.addEventListener('drop', function (e) {
    e.preventDefault(); e.stopPropagation();
    dropZone.classList.remove('drag-over');
    addFiles(Array.from(e.dataTransfer.files));
  });
  document.addEventListener('dragenter', function (e) {
    if (e.dataTransfer && e.dataTransfer.types.includes('Files')) {
      uploadPanel.classList.remove('collapsed');
      uploadToggleBtn.classList.add('active');
    }
  });

  function addFiles(files) {
    files.forEach(function (file) {
      var ext = '.' + file.name.split('.').pop().toLowerCase();
      if (!ACCEPTED_EXT.includes(ext)) return;
      var dup = stagedFiles.some(function (sf) {
        return sf.file.name === file.name && sf.file.size === file.size;
      });
      if (dup) return;
      var uid = 'f_' + Math.random().toString(36).substr(2, 8);
      stagedFiles.push({ file: file, id: uid });
      renderFileItem(file, uid);
    });
    refreshSubmitBtn();
  }

  function renderFileItem(file, uid) {
    var ext  = file.name.split('.').pop().toUpperCase();
    var size = formatFileSize(file.size);
    var safe = escapeHtml(file.name);
    var item = document.createElement('div');
    item.className = 'file-item';
    item.setAttribute('data-uid', uid);
    item.innerHTML =
      '<span class="file-ext-badge">' + ext + '</span>' +
      '<span class="file-name" title="' + safe + '">' + safe + '</span>' +
      '<span class="file-size">' + size + '</span>' +
      '<button class="file-remove" title="Remove"><i class="fa-solid fa-xmark"></i></button>';
    item.querySelector('.file-remove').addEventListener('click', function (e) {
      e.stopPropagation();
      stagedFiles = stagedFiles.filter(function (sf) { return sf.id !== uid; });
      var el = fileList.querySelector('[data-uid="' + uid + '"]');
      if (el) el.remove();
      refreshSubmitBtn();
      setUploadStatus('', '');
    });
    fileList.appendChild(item);
  }

  function refreshSubmitBtn() { uploadSubmitBtn.disabled = stagedFiles.length === 0; }

  uploadSubmitBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (stagedFiles.length === 0) return;
    uploadFiles();
  });

  function uploadFiles() {
    var formData = new FormData();
    stagedFiles.forEach(function (sf) { formData.append('files', sf.file); });
    formData.append('session_id', id);

    stagedFiles.forEach(function (sf) { setItemState(sf.id, 'uploading'); });
    uploadSubmitBtn.disabled = true;
    setUploadStatus('Uploading...', '');

    var headers = {};
    if (currentToken) headers['Authorization'] = 'Bearer ' + currentToken;

    fetch('/upload', { method: 'POST', headers: headers, body: formData })
    .then(function (res) { if (!res.ok) throw new Error('HTTP ' + res.status); return res.json(); })
    .then(function (data) {
      stagedFiles.forEach(function (sf) { setItemState(sf.id, 'success'); });
      setUploadStatus(data.message || 'Files ready.', 'success');
      receiveMessage('📎 ' + stagedFiles.length + ' file' +
        (stagedFiles.length > 1 ? 's' : '') + ' uploaded. You can now ask questions about them.');
      setTimeout(function () { stagedFiles = []; fileList.innerHTML = ''; refreshSubmitBtn(); }, 1200);
    })
    .catch(function () {
      stagedFiles.forEach(function (sf) { setItemState(sf.id, 'error'); });
      setUploadStatus('Upload failed. Try again.', 'error');
      uploadSubmitBtn.disabled = false;
    });
  }

  function setItemState(uid, state) {
    var el = fileList.querySelector('[data-uid="' + uid + '"]');
    if (!el) return;
    el.classList.remove('uploading', 'success', 'error');
    el.classList.add(state);
    var btn = el.querySelector('.file-remove');
    if (btn) btn.style.display = (state === 'uploading') ? 'none' : '';
  }

  function setUploadStatus(msg, type) {
    uploadStatus.textContent = msg;
    uploadStatus.className = 'upload-status' + (type ? ' ' + type : '');
  }

  function formatFileSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
  }

  function escapeHtml(str) {
    return str.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
  }
})();

/* =======================
   AUTO-GROW TEXTAREA
======================= */
(function () {
  var textarea = document.querySelector('.message-input');
  if (!textarea) return;
  function autoGrow() {
    textarea.style.height = 'auto';
    textarea.style.height = textarea.scrollHeight + 'px';
    textarea.style.overflowY = textarea.scrollHeight > textarea.clientHeight ? 'auto' : 'hidden';
  }
  textarea.addEventListener('input', autoGrow);
  setInterval(function () {
    if (textarea.value === '') {
      textarea.style.height = '';
      textarea.style.overflowY = 'hidden';
    }
  }, 150);
})();

/* =======================
   UTILITIES
======================= */
function escapeHtmlInline(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

/* stub so any legacy call to saveToHistory doesn't crash */
window.saveToHistory = function () {};