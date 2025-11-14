
from unittest.mock import patch, MagicMock
from linkedin.actions.profile import get_profile_info


@patch('linkedin.actions.profile.get_profile')
def test_get_profile_info(mock_get_profile):
    """
    Test that the get_profile_info action calls the API and returns the profile data.
    """
    # Arrange
    mock_profile_data = {"firstName": "John", "lastName": "Doe"}
    mock_get_profile.return_value = mock_profile_data

    linkedin_url = "https://www.linkedin.com/in/johndoe/"

    # Act
    result = get_profile_info(None, linkedin_url)

    # Assert
    mock_get_profile.assert_called_once_with(public_id="johndoe")
    assert result == mock_profile_data
