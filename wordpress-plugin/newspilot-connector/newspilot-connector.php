<?php
/**
 * Plugin Name: NewsPilot Connector
 * Description: Securely receives NewsPilot posts, tags, and Yoast SEO metadata.
 * Version: 1.0.0
 */

define( 'NEWSPILOT_CONNECTOR_TOKEN_OPTION', 'newspilot_connector_token' );

function newspilot_connector_activate() {
	if ( ! get_option( NEWSPILOT_CONNECTOR_TOKEN_OPTION ) ) {
		update_option( NEWSPILOT_CONNECTOR_TOKEN_OPTION, wp_generate_password( 64, false, false ), false );
	}
}
register_activation_hook( __FILE__, 'newspilot_connector_activate' );

function newspilot_connector_register_settings_page() {
	add_options_page(
		'NewsPilot Connector',
		'NewsPilot Connector',
		'manage_options',
		'newspilot-connector',
		'newspilot_connector_render_settings_page'
	);
}
add_action( 'admin_menu', 'newspilot_connector_register_settings_page' );

function newspilot_connector_render_settings_page() {
	if ( ! current_user_can( 'manage_options' ) ) {
		return;
	}

	if ( isset( $_POST['newspilot_regenerate_token'] ) ) {
		check_admin_referer( 'newspilot_regenerate_token' );
		update_option( NEWSPILOT_CONNECTOR_TOKEN_OPTION, wp_generate_password( 64, false, false ), false );
		echo '<div class="notice notice-success is-dismissible"><p>NewsPilot token regenerated. Update the token in NewsPilot\'s .env file.</p></div>';
	}

	$token = (string) get_option( NEWSPILOT_CONNECTOR_TOKEN_OPTION, '' );
	?>
	<div class="wrap">
		<h1>NewsPilot Connector</h1>
		<p>Copy this token into <code>WORDPRESS_CONNECTOR_TOKEN</code> in NewsPilot's private <code>.env</code> file.</p>
		<p><strong>Keep this token secret.</strong> Regenerating it immediately disconnects the current NewsPilot configuration until you update its <code>.env</code> file.</p>
		<label for="newspilot-connector-token"><strong>Connector token</strong></label>
		<p><input id="newspilot-connector-token" class="large-text code" type="text" readonly value="<?php echo esc_attr( $token ); ?>"></p>
		<form method="post">
			<?php wp_nonce_field( 'newspilot_regenerate_token' ); ?>
			<?php submit_button( 'Regenerate token', 'secondary', 'newspilot_regenerate_token' ); ?>
		</form>
	</div>
	<?php
}

function newspilot_connector_authorize( $request ) {
	$expected = (string) get_option( NEWSPILOT_CONNECTOR_TOKEN_OPTION, '' );
	$provided = (string) $request->get_header( 'x-newspilot-token' );
	if ( '' === $expected || '' === $provided || ! hash_equals( $expected, $provided ) ) {
		return new WP_Error(
			'newspilot_unauthorized',
			'Invalid NewsPilot connector token.',
			array( 'status' => 401 )
		);
	}
	return true;
}

function newspilot_connector_status() {
	return rest_ensure_response(
		array(
			'connected' => true,
			'site_url'  => home_url( '/' ),
		)
	);
}

function newspilot_connector_save_post( $request ) {
	$params = $request->get_json_params();
	if ( ! is_array( $params ) ) {
		return new WP_Error( 'newspilot_invalid_data', 'Expected a JSON object.', array( 'status' => 400 ) );
	}
	if ( ! isset( $params['title'], $params['content'] ) || ! is_string( $params['title'] ) || ! is_string( $params['content'] ) ) {
		return new WP_Error( 'newspilot_missing_content', 'A title and content are required.', array( 'status' => 400 ) );
	}

	$title = sanitize_text_field( $params['title'] );
	if ( '' === $title || '' === trim( wp_strip_all_tags( $params['content'] ) ) ) {
		return new WP_Error( 'newspilot_empty_content', 'The title and article content cannot be empty.', array( 'status' => 400 ) );
	}

	$tags = array();
	if ( isset( $params['tags'] ) ) {
		if ( ! is_array( $params['tags'] ) ) {
			return new WP_Error( 'newspilot_invalid_tags', 'Tags must be a list of strings.', array( 'status' => 400 ) );
		}
		foreach ( $params['tags'] as $tag ) {
			if ( ! is_string( $tag ) ) {
				return new WP_Error( 'newspilot_invalid_tag', 'Each tag must be a string.', array( 'status' => 400 ) );
			}
			$tag = sanitize_text_field( $tag );
			if ( '' !== $tag ) {
				$tags[] = $tag;
			}
		}
		$tags = array_values( array_unique( $tags ) );
	}
	$yoast = array();
	if ( isset( $params['newspilot_yoast'] ) ) {
		if ( ! is_array( $params['newspilot_yoast'] ) ) {
			return new WP_Error( 'newspilot_invalid_yoast_data', 'Invalid Yoast metadata.', array( 'status' => 400 ) );
		}
		$yoast = $params['newspilot_yoast'];
		foreach ( array( 'seo_title', 'meta_description', 'focus_keyphrase' ) as $field ) {
			if ( isset( $yoast[ $field ] ) && ! is_string( $yoast[ $field ] ) ) {
				return new WP_Error( 'newspilot_invalid_yoast_field', 'Yoast fields must be strings.', array( 'status' => 400 ) );
			}
		}
	}
	if ( isset( $params['excerpt'] ) && ! is_string( $params['excerpt'] ) ) {
		return new WP_Error( 'newspilot_invalid_excerpt', 'The excerpt must be a string.', array( 'status' => 400 ) );
	}

	$existing_post_id = 0;
	if ( isset( $params['existing_post_id'] ) && null !== $params['existing_post_id'] && '' !== $params['existing_post_id'] ) {
		$existing_post_id = filter_var( $params['existing_post_id'], FILTER_VALIDATE_INT );
		$existing_post    = get_post( $existing_post_id );
		if ( false === $existing_post_id || $existing_post_id <= 0 || ! $existing_post || 'post' !== $existing_post->post_type ) {
			return new WP_Error( 'newspilot_post_not_found', 'The linked WordPress post was not found.', array( 'status' => 404 ) );
		}
	}

	$status = isset( $params['status'] ) ? sanitize_key( $params['status'] ) : 'draft';
	if ( ! in_array( $status, array( 'draft', 'publish' ), true ) ) {
		return new WP_Error( 'newspilot_invalid_status', 'Only draft or publish status is allowed.', array( 'status' => 400 ) );
	}

	$post_data = array(
		'post_type'    => 'post',
		'post_title'   => $title,
		'post_content' => wp_kses_post( $params['content'] ),
		'post_excerpt' => isset( $params['excerpt'] ) ? sanitize_textarea_field( $params['excerpt'] ) : '',
	);
	if ( isset( $params['slug'] ) && is_string( $params['slug'] ) && '' !== $params['slug'] ) {
		$post_data['post_name'] = sanitize_title( $params['slug'] );
	}
	if ( $existing_post_id ) {
		$post_data['ID'] = $existing_post_id;
		if ( isset( $params['status'] ) ) {
			$post_data['post_status'] = $status;
		}
	} else {
		$post_data['post_status'] = $status;
	}

	$post_id = wp_insert_post( $post_data, true );
	if ( is_wp_error( $post_id ) ) {
		return $post_id;
	}

	if ( isset( $params['tags'] ) ) {
		$tag_ids = array();
		foreach ( $tags as $tag ) {
			$term = term_exists( $tag, 'post_tag' );
			if ( ! $term ) {
				$term = wp_insert_term( $tag, 'post_tag' );
				if ( is_wp_error( $term ) ) {
					return $term;
				}
			}
			$tag_ids[] = is_array( $term ) ? (int) $term['term_id'] : (int) $term;
		}
		$result = wp_set_post_terms( $post_id, $tag_ids, 'post_tag' );
		if ( is_wp_error( $result ) ) {
			return $result;
		}
	}

	$yoast_fields = array(
		'seo_title'        => '_yoast_wpseo_title',
		'meta_description' => '_yoast_wpseo_metadesc',
		'focus_keyphrase'  => '_yoast_wpseo_focuskw',
	);
	if ( isset( $params['newspilot_yoast'] ) ) {
		foreach ( $yoast_fields as $field => $meta_key ) {
			if ( isset( $yoast[ $field ] ) ) {
				update_post_meta( $post_id, $meta_key, sanitize_text_field( $yoast[ $field ] ) );
			}
		}
	}

	$post = get_post( $post_id );
	return rest_ensure_response(
		array(
			'id'     => (int) $post_id,
			'status' => $post ? $post->post_status : $status,
			'link'   => get_permalink( $post_id ),
		)
	);
}

function newspilot_connector_register_routes() {
	register_rest_route(
		'newspilot/v1',
		'/status',
		array(
			'methods'             => WP_REST_Server::READABLE,
			'callback'            => 'newspilot_connector_status',
			'permission_callback' => 'newspilot_connector_authorize',
		)
	);
	register_rest_route(
		'newspilot/v1',
		'/posts',
		array(
			'methods'             => WP_REST_Server::CREATABLE,
			'callback'            => 'newspilot_connector_save_post',
			'permission_callback' => 'newspilot_connector_authorize',
		)
	);
}
add_action( 'rest_api_init', 'newspilot_connector_register_routes' );
