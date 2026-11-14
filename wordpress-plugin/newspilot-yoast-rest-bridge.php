<?php
/**
 * Plugin Name: NewsPilot Yoast REST Bridge
 * Description: Allows NewsPilot to set Yoast SEO metadata on WordPress posts through the REST API.
 * Version: 1.0.0
 */

add_action(
	'rest_api_init',
	function () {
		register_rest_field(
			'post',
			'newspilot_yoast',
			array(
				'get_callback'    => function ( $post ) {
					$post_id = (int) $post['id'];
					return array(
						'seo_title'        => (string) get_post_meta( $post_id, '_yoast_wpseo_title', true ),
						'meta_description' => (string) get_post_meta( $post_id, '_yoast_wpseo_metadesc', true ),
						'focus_keyphrase'  => (string) get_post_meta( $post_id, '_yoast_wpseo_focuskw', true ),
					);
				},
				'update_callback' => function ( $value, $post ) {
					if ( ! current_user_can( 'edit_post', $post->ID ) ) {
						return new WP_Error( 'newspilot_forbidden', 'You cannot edit this post.', array( 'status' => 403 ) );
					}
					if ( is_object( $value ) ) {
						$value = (array) $value;
					}
					if ( ! is_array( $value ) ) {
						return new WP_Error( 'newspilot_invalid_yoast_data', 'Invalid Yoast metadata.', array( 'status' => 400 ) );
					}

					$meta_fields = array(
						'seo_title'        => '_yoast_wpseo_title',
						'meta_description' => '_yoast_wpseo_metadesc',
						'focus_keyphrase'  => '_yoast_wpseo_focuskw',
					);
					foreach ( $meta_fields as $field => $meta_key ) {
						if ( isset( $value[ $field ] ) ) {
							update_post_meta( $post->ID, $meta_key, sanitize_text_field( $value[ $field ] ) );
						}
					}
					return true;
				},
				'schema'          => array(
					'description' => 'Yoast SEO fields written by NewsPilot.',
					'type'        => 'object',
					'context'     => array( 'view', 'edit' ),
					'properties'  => array(
						'seo_title'        => array( 'type' => 'string' ),
						'meta_description' => array( 'type' => 'string' ),
						'focus_keyphrase'  => array( 'type' => 'string' ),
					),
				),
			)
		);
	}
);
