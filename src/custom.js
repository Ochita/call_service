const uuid=()=>([1e7]+-1e3+-4e3+-8e3+-1e11).replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15 >> c/4).toString(16));
$('#generate').click(function() { $('#groupId').val(uuid()).parent().addClass('is-focused');});
$('#create').click(function() {
   let users = $('#users').val().replace(' ', '').split(',');
   let uid = $('#groupId').val();
   $.ajax({
       url: '/create_group',
       method: 'post',
       contentType: 'application/json',
       dataType: 'json',
       data: JSON.stringify({uid: uid, users: users})
   })
});