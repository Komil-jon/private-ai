/* ============================================================
   script.js  —  Obelius Private AI
   ============================================================ */

/* =======================
   MARKED.JS CONFIG
   Open all links rendered from markdown in a new tab.
======================= */
marked.use({
  renderer: {
    link: function (token) {
      var href  = token.href  || '';
      var title = token.title || '';
      var text  = token.text  || '';
      var t = title ? ' title="' + title + '"' : '';
      return '<a href="' + href + '"' + t + ' target="_blank" rel="noopener noreferrer">' + text + '</a>';
    }
  }
});

/* =======================
   STATE
======================= */
var currentUser      = null;
var currentToken     = null;
var activeConvId     = null;
var conversationList = [];   // in-memory cache of loaded conversations

// Guest session id — used as document-store key for uploads
var id = getOrCreateSessionId();

var $messages = $('.messages-content');
var d, h, m;

/* =======================
   INITIAL LOAD
======================= */
$(window).on('load', function () {
  setTimeout(function () {
    showLoadingMessage();
    checkAuth();
  }, 100);
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
   AUTH — check on page load

   Flow:
   1. No token  → show guest greeting
   2. Token invalid → clear it, show guest greeting
   3. Token valid, has lastConv → load that conversation
   4. Token valid, no lastConv → auto-create new conv, show greeting
======================= */
var AUTH_BASE = 'https://auth.eternal.uz';

/* Pick up token from URL after OAuth redirect, then clean the URL */
(function handleAuthCallback() {
  // Check query params (?token= or ?eternal_token=)
  var params = new URLSearchParams(window.location.search);
  var urlToken = params.get('token') || params.get('eternal_token');

  // Also check hash fragment (#token= or #eternal_token=)
  if (!urlToken && window.location.hash) {
    var hashParams = new URLSearchParams(window.location.hash.slice(1));
    urlToken = hashParams.get('token') || hashParams.get('eternal_token');
  }

  if (urlToken) {
    localStorage.setItem('eternal_token', urlToken);
    params.delete('token');
    params.delete('eternal_token');
    var clean = window.location.pathname + (params.toString() ? '?' + params.toString() : '');
    history.replaceState(null, '', clean);
  }
})();

async function checkAuth() {
  try {
    // 1. Prefer an explicit token in localStorage
    var token = localStorage.getItem('eternal_token');

    // 2. Fall back to extracting the token from eternal_user (auth server stores it there)
    if (!token) {
      var storedUser = localStorage.getItem('eternal_user');
      if (storedUser) {
        try {
          var parsedUser = JSON.parse(storedUser);
          // Auth server may embed the token inside the user object
          if (parsedUser.token) {
            token = parsedUser.token;
            localStorage.setItem('eternal_token', token);
          } else {
            // No token field — trust the stored user data directly
            currentToken = null;
            currentUser  = parsedUser;
            renderAuthState(currentUser);
            await loadConversations();
            var lastConv = localStorage.getItem('obelius_last_conv');
            if (lastConv) {
              var restored = await openConversation(lastConv, true);
              if (!restored) {
                localStorage.removeItem('obelius_last_conv');
                await startFreshConversation();
              }
            } else {
              await startFreshConversation();
            }
            return;
          }
        } catch (e) { /* malformed JSON — ignore */ }
      }
    }

    if (!token) {
      renderAuthState(null);
      hideLoadingMessage();
      firstMessage();
      return;
    }

    var resp = await fetch(AUTH_BASE + '/api/verify', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ token: token }),
    });

    if (!resp.ok) {
      renderAuthState(null);
      hideLoadingMessage();
      firstMessage();
      return;
    }

    var data = await resp.json();
    if (!data.valid) {
      localStorage.removeItem('eternal_token');
      renderAuthState(null);
      hideLoadingMessage();
      firstMessage();
      return;
    }

    // ── Authenticated ────────────────────────────────────────
    currentToken = token;
    currentUser  = data.user;
    renderAuthState(currentUser);

    // Load conversation list for the drawer
    await loadConversations();

    var lastConv = localStorage.getItem('obelius_last_conv');
    if (lastConv) {
      // Try to restore the last conversation
      var restored = await openConversation(lastConv, true);
      if (!restored) {
        // That conversation no longer exists — start fresh
        localStorage.removeItem('obelius_last_conv');
        await startFreshConversation();
      }
    } else {
      // No previous conversation — start a fresh one
      await startFreshConversation();
    }

  } catch (e) {
    console.warn('[auth] check failed:', e);
    renderAuthState(null);
    hideLoadingMessage();
    firstMessage();
  }
}

async function startFreshConversation() {
  if (!currentUser) return;

  // Reuse the first untitled conversation if one already exists —
  // avoids piling up blank "New conversation" entries on every login/reload.
  var existing = conversationList.find(function (c) {
    return c.title === 'New conversation';
  });
  if (existing) {
    activeConvId = existing.id;
    localStorage.setItem('obelius_last_conv', existing.id);
    // Highlight it in the sidebar
    document.querySelectorAll('.conv-item').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('data-id') === existing.id);
    });
    hideLoadingMessage();
    firstMessage();
    return;
  }

  // No untitled conversation exists — create one
  try {
    var resp = await authFetch('/api/conversations', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({}),
    });
    if (resp.ok) {
      var conv = await resp.json();
      activeConvId = conv.id;
      localStorage.setItem('obelius_last_conv', conv.id);
      await loadConversations();
    }
  } catch (e) {
    console.warn('[conv] startFresh failed:', e);
  }
  hideLoadingMessage();
  firstMessage();
}

/* =======================
   AUTH FETCH HELPER
   Builds headers + credentials for API calls.
   Sends Bearer token when available, falls back to cookies.
======================= */
function authFetch(url, options) {
  options = options || {};
  var headers = Object.assign({}, options.headers || {});
  if (currentToken) {
    headers['Authorization'] = 'Bearer ' + currentToken;
  }
  options.headers     = headers;
  options.credentials = 'include'; // send eternal_token cookie as fallback
  return fetch(url, options);
}

/* =======================
   AUTH STATE RENDER
======================= */
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
   AUTH — sign in / out
======================= */
document.getElementById('drawer-login-btn').addEventListener('click', function (e) {
  e.preventDefault();
  var redirect = encodeURIComponent(window.location.href);
  // prompt=login tells the auth server to always show the login form,
  // even if a valid session already exists there (standard OAuth2 pattern).
  window.location.href = AUTH_BASE + '?redirect=' + redirect + '&prompt=login';
});

document.getElementById('drawer-logout-btn').addEventListener('click', function () {
  localStorage.removeItem('eternal_token');
  localStorage.removeItem('eternal_user');
  localStorage.removeItem('obelius_last_conv');
  currentToken     = null;
  currentUser      = null;
  activeConvId     = null;
  conversationList = [];
  renderAuthState(null);
  clearChat();
  firstMessage();
});

/* =======================
   CONVERSATIONS — load list
======================= */
async function loadConversations() {
  if (!currentUser) return;
  try {
    var resp = await authFetch('/api/conversations');
    if (!resp.ok) return;
    var convs = await resp.json();
    renderConversationList(convs);
  } catch (e) {
    console.warn('[convs] load failed:', e);
  }
}

function renderConversationList(convs) {
  conversationList = convs || [];   // keep cache in sync
  var list = document.getElementById('conv-list');
  if (!list) return;

  if (!conversationList.length) {
    list.innerHTML = '<div class="conv-empty">No conversations yet.</div>';
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
      closeDrawer();
    });

    item.querySelector('.conv-item-delete').addEventListener('click', function (e) {
      e.stopPropagation();
      deleteConversation(conv.id);
    });

    list.appendChild(item);
  });
}

/* =======================
   CONVERSATIONS — open
   Returns true if conversation was found and loaded, false if not found.
======================= */
async function openConversation(convId, clearScreen) {
  if (!currentUser) return false;

  try {
    var resp = await authFetch('/api/conversations/' + convId + '/messages');

    // 404 means conversation no longer exists
    if (resp.status === 404) return false;
    if (!resp.ok) return false;

    var msgs = await resp.json();

    activeConvId = convId;
    localStorage.setItem('obelius_last_conv', convId);

    // Highlight active in list
    document.querySelectorAll('.conv-item').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('data-id') === convId);
    });

    if (clearScreen) clearChat();

    if (msgs.length === 0) {
      // Empty conversation — show greeting
      hideLoadingMessage();
      firstMessage();
      return true;
    }

    // Render all messages
    msgs.forEach(function (msg) {
      if (msg.role === 'user') {
        $('<div class="message message-personal">' + escapeHtmlInline(msg.content) + '</div>')
          .appendTo($('.messages-content'));
      } else {
        var bubble  = $('<div class="message new"></div>');
        var avatar  = '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>';
        var content = $('<div class="message-content"></div>').html(marked.parse(msg.content));
        bubble.append(avatar);
        // Re-render search indicator if this message was backed by a web search
        if (msg.search_info) {
          var si = msg.search_info;
          var siQueries = si.queries && si.queries.length ? si.queries : (si.query ? [si.query] : []);
          if (siQueries.length) {
            bubble.append($('<div>').addClass('search-indicator').html(
              buildSearchLabel(siQueries, si.results_count)
            ));
          }
        }
        bubble.append(content);
        if (msg.sources && msg.sources.length > 0) {
          bubble.append(buildSourceChips(msg.sources));
        }
        $('.messages-content').append(bubble);
      }
    });

    hideLoadingMessage();
    setDate();
    updateScrollbar();
    return true;

  } catch (e) {
    console.warn('[conv] open failed:', e);
    return false;
  }
}

/* =======================
   CONVERSATIONS — new
======================= */
async function newConversation() {
  if (!currentUser) return;

  // If the current conversation has no user messages yet, there is nothing
  // to leave behind — just close the drawer, we're already "new".
  var hasMessages = $('.messages-content').find('.message-personal').length > 0;
  if (!hasMessages && activeConvId) {
    closeDrawer();
    return;
  }

  // Current conversation has content — find or create a blank one.
  // Prefer reusing an existing untitled conversation over creating a new one.
  var existing = conversationList.find(function (c) {
    return c.title === 'New conversation' && c.id !== activeConvId;
  });
  if (existing) {
    activeConvId = existing.id;
    localStorage.setItem('obelius_last_conv', existing.id);
    clearChat();
    firstMessage();
    document.querySelectorAll('.conv-item').forEach(function (el) {
      el.classList.toggle('active', el.getAttribute('data-id') === existing.id);
    });
    closeDrawer();
    return;
  }

  try {
    var resp = await authFetch('/api/conversations', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({}),
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

/* =======================
   CONVERSATIONS — delete
======================= */
async function deleteConversation(convId) {
  if (!currentUser) return;
  try {
    await authFetch('/api/conversations/' + convId, { method: 'DELETE' });
    if (activeConvId === convId) {
      activeConvId = null;
      localStorage.removeItem('obelius_last_conv');
      clearChat();
      // Start a new blank conversation
      await startFreshConversation();
    }
    await loadConversations();
  } catch (e) {
    console.warn('[conv] delete failed:', e);
  }
}

async function deleteAllConversations() {
  if (!currentUser) return;
  try {
    await authFetch('/api/conversations/all', { method: 'DELETE' });
    activeConvId = null;
    localStorage.removeItem('obelius_last_conv');
    clearChat();
    await startFreshConversation();
    await loadConversations();
  } catch (e) {
    console.warn('[conv] delete all failed:', e);
  }
}

function clearChat() {
  $('.messages-content').empty();
}

/* =======================
   DRAWER TOGGLE
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
      if (currentUser) loadConversations();
      drawer.classList.remove('collapsed');
      toggleBtn.classList.add('active');
    }
  });

  document.addEventListener('click', function (e) {
    var chat = document.querySelector('.chat');
    if (chat && !chat.contains(e.target)) closeDrawer();
  });
})();

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
var _scrollPending = false;
function updateScrollbar() {
  if (_scrollPending) return;
  _scrollPending = true;
  requestAnimationFrame(function () {
    var el = document.querySelector('.messages');
    if (el) el.scrollTop = el.scrollHeight;
    _scrollPending = false;
  });
}

/* =======================
   TOKEN QUEUE  (batch per animation frame for smooth streaming)
======================= */
var _tokenBubble  = null;
var _tokenBuf     = '';
var _tokenRafPending = false;

function flushTokenBuffer() {
  if (_tokenBubble && _tokenBuf) {
    appendTokenToBubble(_tokenBubble, _tokenBuf);
    _tokenBuf = '';
    var el = document.querySelector('.messages');
    if (el) el.scrollTop = el.scrollHeight;
  }
  _tokenRafPending = false;
}

function queueToken(bubble, text) {
  _tokenBubble = bubble;
  _tokenBuf   += text;
  if (!_tokenRafPending) {
    _tokenRafPending = true;
    requestAnimationFrame(flushTokenBuffer);
  }
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

  // Flush any in-progress streaming token before appending the user message
  if (_tokenBuf && _tokenBubble) { flushTokenBuffer(); }

  $('<div class="message message-personal">' + escapeHtmlInline(msg) + '</div>')
    .appendTo($('.messages-content'))
    .addClass('new');

  setDate();
  $('.message-input').val(null);

  // Defer scroll one frame so the new message is in the DOM before we measure
  requestAnimationFrame(function () {
    var el = document.querySelector('.messages');
    if (el) el.scrollTop = el.scrollHeight;
  });

  sendMessageToServer(msg);
}

$('.message-submit').on('click', function () { insertMessage(); });

$(window).on('keydown', function (e) {
  if (e.which === 13 && !e.shiftKey) { insertMessage(); return false; }
});

/* =======================
   SERVER COMMUNICATION (streaming SSE)
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

  // Always include conv_id if we have one — backend uses it to find/create the conversation
  if (activeConvId) body.conv_id = activeConvId;

  fetch('/stream', { method: 'POST', headers: headers, body: JSON.stringify(body), credentials: 'include' })
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

          if (evt.type === 'search_info') {
            if (evt.triggered) {
              var queries = evt.queries && evt.queries.length ? evt.queries : (evt.query ? [evt.query] : []);
              var indicator = $('<div>').addClass('search-indicator').html(buildSearchLabel(queries, evt.results_count));
              bubble.find('.message-content').before(indicator);
              updateScrollbar();
            }
          }

          else if (evt.type === 'token') {
            accumulated += evt.text;
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

            queueToken(bubble, evt.text);
          }

          else if (evt.type === 'sources') {
            finaliseStreamingBubble(bubble, accumulated, evt.sources || []);
            setDate();
            updateScrollbar();
          }

          else if (evt.type === 'done') {
            // Backend tells us which conv_id was used/created
            if (evt.conv_id && evt.conv_id !== id) {
              var wasNew = !activeConvId || activeConvId !== evt.conv_id;
              activeConvId = evt.conv_id;
              localStorage.setItem('obelius_last_conv', activeConvId);
              // Refresh drawer list (title may have been auto-set)
              if (currentUser) {
                setTimeout(loadConversations, 400);
              }
            }
          }

          else if (evt.type === 'error') {
            finaliseStreamingBubble(bubble, evt.text || 'Something went wrong.', []);
            setDate();
            updateScrollbar();
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
  $('.messages-content').append(bubble);
  updateScrollbar();
  return bubble;
}

function appendTokenToBubble(bubble, token) {
  var cursor = bubble.find('.stream-cursor');
  cursor.before(document.createTextNode(token));
}

function finaliseStreamingBubble(bubble, fullText, sources) {
  // Flush any buffered tokens before finalising
  if (_tokenBuf && _tokenBubble === bubble) { flushTokenBuffer(); }
  _tokenBubble = null; _tokenBuf = '';

  var content = bubble.find('.message-content');
  content.find('.stream-cursor').remove();
  bubble.removeClass('streaming');
  if (fullText && fullText.trim()) {
    content.html(marked.parse(fullText));
  }
  if (sources && sources.length > 0) {
    bubble.append(buildSourceChips(sources));
  }
}

function buildSourceChips(sources) {
  // Deduplicate doc chips: one chip per filename, highest score wins
  var docMap = {};
  var webList = [];
  (sources || []).forEach(function (src) {
    if (src.type === 'web') {
      webList.push(src);
    } else {
      var key = src.title || '';
      if (!docMap[key] || (src.score || 0) > (docMap[key].score || 0)) {
        docMap[key] = src;
      }
    }
  });
  var deduped = Object.values(docMap).concat(webList);
  if (!deduped.length) return $('');

  var html = '<div class="source-chips">';
  deduped.forEach(function (src, i) {
    var label = src.title || src.file || src.name || ('Source ' + (i + 1));
    var score = src.score ? ' · ' + Math.round(src.score * 100) + '%' : '';

    if (src.type === 'web' && src.url) {
      html +=
        '<a class="source-chip web-chip" href="' + escapeHtmlInline(src.url) + '" ' +
        'target="_blank" rel="noopener noreferrer" title="' + escapeHtmlInline(src.url) + '">' +
        '<i class="fa-solid fa-globe"></i> ' + escapeHtmlInline(label) +
        '</a>';
    } else {
      html +=
        '<span class="source-chip" title="' + escapeHtmlInline(label + score) + '">' +
        '<i class="fa-solid fa-bookmark"></i> ' + escapeHtmlInline(label) + score +
        '</span>';
    }
  });
  html += '</div>';
  return $(html);
}

/* =======================
   RECEIVE MESSAGE (system / file upload confirmations)
======================= */
function receiveMessage(message, sources) {
  hideLoadingMessage();
  var bubble  = $('<div class="message new"></div>');
  var avatar  = '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>';
  var content = $('<div class="message-content"></div>').html(marked.parse(message));
  bubble.append(avatar).append(content);
  if (sources && sources.length > 0) bubble.append(buildSourceChips(sources));
  $('.messages-content').append(bubble);
  setDate();
  updateScrollbar();
}

/* =======================
   FIRST / LOADING MESSAGES
======================= */
function firstMessage() {
  hideLoadingMessage();
  var greeting = currentUser
    ? 'Hello, ' + (currentUser.full_name || currentUser.username) + '! How can I help you today?'
    : 'Hello! How can I help you today?';

  $('<div class="message new">' +
    '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>' +
    '<div class="message-content">' + escapeHtmlInline(greeting) + '</div>' +
    '</div>')
    .appendTo($('.messages-content'));

  setDate();
  updateScrollbar();
}

function showLoadingMessage() {
  $('.message.loading').remove(); // prevent duplicates
  $('<div class="message loading new">' +
    '<figure class="avatar"><img src="/static/images/icon.svg" /></figure>' +
    '<span>Loading...</span></div>')
    .appendTo($('.messages-content'));
  updateScrollbar();
}

function hideLoadingMessage() {
  $('.message.loading').remove();
}

/* =======================
   COLLECT CHAT (guest context for AI)
======================= */
function collectVisibleMessages() {
  var conversation = [];
  $('.messages-content .message').not('.loading').each(function () {
    if (!$(this).is(':visible')) return;
    var contentEl = $(this).find('.message-content');
    var text = (contentEl.length ? contentEl.text() : $(this).text()).trim();
    var isUser = $(this).hasClass('message-personal');
    if (text && text !== 'Loading...') {
      conversation.push({ role: isUser ? 'user' : 'assistant', content: text });
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
    // Tag upload to the current conversation so documents don't bleed across chats
    if (activeConvId) formData.append('conv_id', activeConvId);

    stagedFiles.forEach(function (sf) { setItemState(sf.id, 'uploading'); });
    uploadSubmitBtn.disabled = true;
    setUploadStatus('Uploading...', '');

    var headers = {};
    if (currentToken) headers['Authorization'] = 'Bearer ' + currentToken;

    fetch('/upload', { method: 'POST', headers: headers, body: formData, credentials: 'include' })
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
  var MAX_H = 8 * window.innerHeight / 100; // 8vh in px
  function autoGrow() {
    textarea.style.height = '0';
    var newH = Math.min(textarea.scrollHeight, MAX_H);
    textarea.style.height = newH + 'px';
    textarea.style.overflowY = textarea.scrollHeight > MAX_H ? 'auto' : 'hidden';
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
   SEARCH INDICATOR HELPER
======================= */
function buildSearchLabel(queries, resultsCount) {
  var MAX_Q_CHARS = 48;
  var queryText;
  if (!queries || !queries.length) {
    queryText = 'web';
  } else if (queries.length > 1) {
    queryText = queries.length + ' searches';
  } else {
    var q = queries[0];
    if (q.length > MAX_Q_CHARS) q = q.slice(0, MAX_Q_CHARS) + '…';
    queryText = '&ldquo;' + escapeHtmlInline(q) + '&rdquo;';
  }
  var count = resultsCount || 0;
  return count > 0
    ? '&#x1F50D; Searched ' + queryText + ' &middot; ' + count + ' result' + (count !== 1 ? 's' : '')
    : '&#x1F50D; Searched ' + queryText + ' &middot; no results';
}

/* =======================
   UTILITIES
======================= */
function escapeHtmlInline(str) {
  return String(str)
    .replace(/&/g,'&amp;').replace(/</g,'&lt;')
    .replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

window.saveToHistory = function () {};