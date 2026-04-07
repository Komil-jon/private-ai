var isFirstResponse = true;

var $messages = $('.messages-content'),
    d, h, m,
    id = generateSessionId();

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

  if (!messageBox) return; // 🔴 prevents your error

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
   SERVER COMMUNICATION
======================= */
function sendMessageToServer(message) {

  setTimeout(function () {
    showLoadingMessage();
  }, 0);

  const conversation = collectVisibleMessages();

  if (conversation.length === 0) {
    console.warn('No conversation data to send.');
    return;
  }

  $.ajax({
    url: '/process',
    type: 'POST',
    contentType: 'application/json',
    data: JSON.stringify({
      type: "request",
      data: conversation,
      id: id
    }),

    success: function (response) {

      if (response.response === 'IGNORED') {
        showAlert('Your activity has been reported!');

        $('.messages-content .message').fadeOut(500);

        setTimeout(function () {
          showLoadingMessage();

          setTimeout(function () {
            firstMessage();
          }, 2000);

        }, 100);

        return;
      }

      if (response.response === 'PERSONAL') {
        showAlert('That seems like a personal question.');
        $('.message.loading.new').fadeOut(500);
        return;
      }

      receiveMessage(response.response);
    },

    error: function () {
      console.error('Error in sending message.');
      hideLoadingMessage();
    }
  });
}

/* =======================
   RECEIVE MESSAGE
======================= */
function receiveMessage(message) {
  hideLoadingMessage();

  let messageHtml = $('<div class="message new"></div>');
  let avatarHtml = '<figure class="avatar"><img src="/static/images/claude-ai-icon.svg" /></figure>';

  let formattedMessage = marked.parse(message);

  messageHtml
    .append(avatarHtml)
    .append($('<div></div>').html(formattedMessage));

  $('.mCSB_container').append(messageHtml.addClass('new'));

  setDate();
  updateScrollbar();
}

/* =======================
   DEFAULT / LOADING
======================= */
function firstMessage() {
  let firstMsg = "Hello";

  hideLoadingMessage();

  $('<div class="message new">' +
    '<figure class="avatar"><img src="/static/images/claude-ai-icon.svg" /></figure>' +
    firstMsg +
    '</div>')
    .appendTo($('.mCSB_container'))
    .addClass('new');

  setDate();
  updateScrollbar();
}

function showLoadingMessage() {
  $('<div class="message loading new">' +
    '<figure class="avatar"><img src="/static/images/claude-ai-icon.svg" /></figure>' +
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